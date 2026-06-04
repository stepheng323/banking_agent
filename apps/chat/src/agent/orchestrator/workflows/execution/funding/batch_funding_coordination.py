from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.funding.batch_funding_demands import (
    _build_transfer_demand,
    _is_plannable_transfer_task,
)
from apps.chat.src.agent.orchestrator.workflows.execution.funding.batch_funding_payloads import (
    _funding_plan_to_payload_dict,
)
from apps.chat.src.agent.orchestrator.workflows.execution.result_patch import ExecutionResultPatch
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.presentation.i18n.renderer import render_message
from banking.transfers.funding.coordinator import BatchFundingCoordinator
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _maybe_coordinate_batch_funding(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    services: OrchestrationServices,
    locale: str,
) -> dict[str, Any] | None:
    transfer_task_ids = [task_id for task_id in current_wave if _is_plannable_transfer_task(state.tasks.get(task_id))]
    if len(transfer_task_ids) < 2:
        return None

    transfer_worker = services.transfer
    dd_provider = getattr(transfer_worker, "dd_provider", None) if transfer_worker else None
    if dd_provider is None:
        logger.info("batch_funding_coordinator_skipped", reason="dd_provider_missing", task_ids=transfer_task_ids)
        return None

    accounts_raw = (state.loaded_context or {}).get("accounts") or []
    transaction_accounts_raw = (state.loaded_context or {}).get("transaction_accounts") or accounts_raw
    accounts = [account for account in transaction_accounts_raw if isinstance(account, dict)]
    demands = [
        _build_transfer_demand(task_id, cast(dict[str, Any], state.tasks[task_id].payload))
        for task_id in transfer_task_ids
    ]
    coordinator = BatchFundingCoordinator(dd_provider=dd_provider)
    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale=locale)
    if result.is_feasible:
        for task_id in transfer_task_ids:
            plan = result.plans_by_task.get(task_id)
            if plan is None:
                continue
            payload = state.tasks[task_id].payload
            if not isinstance(payload, dict):
                continue
            payload["funding_plan"] = _funding_plan_to_payload_dict(plan, payload)
        logger.info(
            "batch_funding_coordinator_applied",
            task_count=len(result.plans_by_task),
            total_demanded=result.total_demanded,
            total_available=result.total_available,
        )
        return None

    prompt = result.suggestion or render_message("funding.batch.total_infeasible", locale)
    interrupt = PendingInterrupt(
        kind="input",
        task_ids=transfer_task_ids,
        fields_by_task={task_id: ["funding_plan"] for task_id in transfer_task_ids},
        prompt=prompt,
    )
    logger.info(
        "batch_funding_coordinator_blocked",
        task_count=len(transfer_task_ids),
        shortfall_count=len(result.shortfalls or []),
        total_demanded=result.total_demanded,
        total_available=result.total_available,
    )
    patch = ExecutionResultPatch({"tasks": state.tasks})
    patch.set_pending_interrupt(interrupt)
    patch.set_outbox(_with_policy_notice(state, [{"type": "say", "text": prompt}]))
    patch.clear_policy_notice()
    return patch.to_updates()


__all__ = ["_maybe_coordinate_batch_funding"]
