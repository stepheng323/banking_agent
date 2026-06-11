from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.funding.batch_funding_demands import (
    _batch_transfer_tasks_for_wave,
    _batch_transfer_tasks_not_ready_for_funding,
    _build_transfer_demand,
    _is_plannable_transfer_task,
)
from apps.chat.src.agent.orchestrator.workflows.execution.funding.batch_funding_payloads import (
    _funding_plan_to_payload_dict,
)
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.presentation.i18n.renderer import render_message
from banking.transfers.funding.coordinator import BatchFundingCoordinator
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _funding_interrupt_metadata(result: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "intent": "batch_funding_adjustment",
        "anchor_source_ids": list(getattr(result, "anchor_source_ids", []) or []),
        "suggested_source_ids": list(getattr(result, "suggested_source_ids", []) or []),
    }
    source_choice = getattr(result, "source_choice", None)
    if source_choice is not None:
        metadata["candidate_source_ids"] = list(getattr(source_choice, "candidate_source_ids", []) or [])
        metadata["remaining_amount"] = str(getattr(source_choice, "remaining_amount", "") or "")
    return metadata


def _default_source_account(accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((account for account in accounts if account.get("is_default")), None)


def _apply_default_auto_source_if_missing(payload: dict[str, Any], default_account: dict[str, Any] | None) -> None:
    if default_account is None or payload.get("source_account_id"):
        return
    if payload.get("source_affinity_mode") == "explicit":
        return
    if payload.get("source_bank_name") or payload.get("source_accounts") or payload.get("explicit_split"):
        return

    source_account_id = default_account.get("id")
    if not source_account_id:
        return

    payload["source_account_id"] = str(source_account_id)
    if default_account.get("bank_name"):
        payload["source_bank_name"] = default_account["bank_name"]
    account_number = default_account.get("account_number") or default_account.get("number")
    if account_number:
        payload["source_account_number"] = account_number
    payload.setdefault("source_affinity_mode", "auto")


async def _maybe_coordinate_batch_funding(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    services: OrchestrationServices,
    agg: ExecutionAccumulator,
    locale: str,
    allow_existing_funding_plans: bool = False,
) -> dict[str, Any] | None:
    batch_transfer_tasks = _batch_transfer_tasks_for_wave(state, current_wave)
    if len(batch_transfer_tasks) < 2:
        return None

    waiting_task_ids = _batch_transfer_tasks_not_ready_for_funding(state, current_wave)
    if waiting_task_ids:
        logger.debug(
            "batch_funding_coordinator_waiting_for_recipient_readiness",
            task_count=len(batch_transfer_tasks),
            waiting_task_count=len(waiting_task_ids),
        )
        return None

    transfer_tasks = [
        (task_id, task)
        for task_id, task in batch_transfer_tasks
        if _is_plannable_transfer_task(task, allow_existing_funding_plan=allow_existing_funding_plans)
    ]
    transfer_task_ids = [task_id for task_id, _task in transfer_tasks]
    if len(transfer_task_ids) != len(batch_transfer_tasks) or len(transfer_task_ids) < 2:
        logger.debug(
            "batch_funding_coordinator_waiting_for_full_plannable_batch",
            batch_task_count=len(batch_transfer_tasks),
            plannable_task_count=len(transfer_task_ids),
            replan_existing=allow_existing_funding_plans,
        )
        return None

    transfer_worker = services.transfer
    dd_provider = getattr(transfer_worker, "dd_provider", None) if transfer_worker else None
    if dd_provider is None:
        logger.info("batch_funding_coordinator_skipped", reason="dd_provider_missing", task_ids=transfer_task_ids)
        return None

    accounts = loaded_context(state).transaction_account_rows_or_account_rows
    default_account = _default_source_account(accounts)
    for _task_id, task in transfer_tasks:
        payload = task.payload
        if isinstance(payload, dict):
            _apply_default_auto_source_if_missing(payload, default_account)
    demands = [_build_transfer_demand(task_id, cast(dict[str, Any], task.payload)) for task_id, task in transfer_tasks]
    coordinator = BatchFundingCoordinator(dd_provider=dd_provider)
    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale=locale)
    if result.is_feasible:
        for task_id, task in transfer_tasks:
            plan = result.plans_by_task.get(task_id)
            if plan is None:
                continue
            payload = task.payload
            if not isinstance(payload, dict):
                continue
            payload["funding_plan"] = _funding_plan_to_payload_dict(plan, payload)
            payload["suggested_funding_plan"] = None
        logger.info(
            "batch_funding_coordinator_applied",
            task_count=len(result.plans_by_task),
            total_demanded=result.total_demanded,
            total_available=result.total_available,
            replan_existing=allow_existing_funding_plans,
        )
        return None

    if getattr(result, "requires_source_choice", False):
        for _task_id, task in transfer_tasks:
            task.stage = TaskStage.AWAITING_FUNDING_ADJUSTMENT
        prompt = result.suggestion or render_message("funding.batch.total_infeasible", locale)
        fields_by_task = {task_id: ["source_accounts", "funding_plan"] for task_id in transfer_task_ids}
        logger.info(
            "batch_funding_coordinator_source_choice_blocked",
            task_count=len(transfer_task_ids),
            total_demanded=result.total_demanded,
            total_available=result.total_available,
            replan_existing=allow_existing_funding_plans,
        )
        agg.set_input_interrupt_outbox(
            task_ids=transfer_task_ids,
            fields_by_task=fields_by_task,
            prompt=prompt,
            entries=_with_policy_notice(state, [{"type": "say", "text": prompt}]),
            metadata=_funding_interrupt_metadata(result),
        )
        agg.clear_policy_notice()
        return agg.to_updates()

    if result.requires_user_approval:
        for task_id, task in transfer_tasks:
            plan = result.suggested_plans_by_task.get(task_id)
            if plan is None:
                continue
            payload = task.payload
            if not isinstance(payload, dict):
                continue
            payload["suggested_funding_plan"] = _funding_plan_to_payload_dict(plan, payload)
            payload["funding_plan"] = None
            task.stage = TaskStage.AWAITING_FUNDING_ADJUSTMENT
        prompt = result.suggestion or render_message("funding.batch.total_infeasible", locale)
        fields_by_task = {
            task_id: ["suggested_funding_plan", "amount", "source_accounts", "explicit_split"]
            for task_id in transfer_task_ids
        }
        logger.info(
            "batch_funding_coordinator_suggestion_blocked",
            task_count=len(result.suggested_plans_by_task),
            total_demanded=result.total_demanded,
            total_available=result.total_available,
            replan_existing=allow_existing_funding_plans,
        )
        agg.set_input_interrupt_outbox(
            task_ids=transfer_task_ids,
            fields_by_task=fields_by_task,
            prompt=prompt,
            entries=_with_policy_notice(state, [{"type": "say", "text": prompt}]),
            metadata=_funding_interrupt_metadata(result),
        )
        agg.clear_policy_notice()
        return agg.to_updates()

    prompt = result.suggestion or render_message("funding.batch.total_infeasible", locale)
    fields_by_task = {task_id: ["funding_plan"] for task_id in transfer_task_ids}
    for _task_id, task in transfer_tasks:
        task.stage = TaskStage.AWAITING_FUNDING_ADJUSTMENT
    logger.info(
        "batch_funding_coordinator_blocked",
        task_count=len(transfer_task_ids),
        shortfall_count=len(result.shortfalls or []),
        total_demanded=result.total_demanded,
        total_available=result.total_available,
        replan_existing=allow_existing_funding_plans,
    )
    agg.set_input_interrupt_outbox(
        task_ids=transfer_task_ids,
        fields_by_task=fields_by_task,
        prompt=prompt,
        entries=_with_policy_notice(state, [{"type": "say", "text": prompt}]),
        metadata=_funding_interrupt_metadata(result),
    )
    agg.clear_policy_notice()
    return agg.to_updates()


__all__ = ["_maybe_coordinate_batch_funding"]
