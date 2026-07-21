from collections.abc import Collection
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import recipient_review_signature
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import existing_tasks, get_task
from banking.transfers.funding.batch_models import SourceAffinity, TransferDemand
from shared.money import MoneyAmount, require_naira, to_naira


def _build_transfer_demand(task_id: str, task_type: str, task_payload: dict[str, Any]) -> TransferDemand:
    # A bill can only be debited from one selected linked account.  It must
    # never inherit transfer pooling merely because it shares a wave.
    is_bill = task_type in ("airtime", "data")
    mode = "explicit" if is_bill or task_payload.get("source_affinity_mode") == "explicit" else "auto"
    source_accounts_raw = task_payload.get("source_accounts")
    source_accounts = source_accounts_raw if isinstance(source_accounts_raw, list) else []
    explicit_sources = [str(bank) for bank in source_accounts if str(bank).strip()]
    source_bank_name = task_payload.get("source_bank_name")
    if mode == "explicit" and not explicit_sources and isinstance(source_bank_name, str) and source_bank_name.strip():
        explicit_sources = [source_bank_name.strip()]

    explicit_split_raw = task_payload.get("explicit_split")
    explicit_split: dict[str, MoneyAmount] | None = None
    if isinstance(explicit_split_raw, dict):
        explicit_split = {
            str(key): amount
            for key, value in explicit_split_raw.items()
            if str(key).strip() and (amount := to_naira(value)) is not None and amount > 0
        }
    preferred_account_id = task_payload.get("source_account_id")

    source_pooling_locked = bool(task_payload.get("source_pooling_locked"))
    if is_bill:
        source_pooling_locked = True

    return TransferDemand(
        task_id=task_id,
        amount=to_naira(task_payload.get("amount")) or require_naira(0),
        source_affinity=SourceAffinity(mode=cast(Any, mode)),
        explicit_sources=explicit_sources,
        explicit_split=explicit_split,
        use_dual_accounts=bool(task_payload.get("use_dual_accounts")),
        preferred_account_id=str(preferred_account_id) if preferred_account_id else None,
        source_pooling_locked=source_pooling_locked,
    )


_BATCH_FUNDING_MUTABLE_STAGES = {
    TaskStage.DRAFT,
    TaskStage.EXTRACTED,
    TaskStage.RESOLVED,
    TaskStage.VALIDATED,
    TaskStage.AWAITING_FUNDING_ADJUSTMENT,
    TaskStage.AWAITING_CONFIRMATION,
}


def _has_recipient_destination(payload: dict[str, Any], task_type: str) -> bool:
    if task_type == "transfer":
        recipient_account = payload.get("recipient_account") or payload.get("recipient_account_number")
        recipient_bank = payload.get("recipient_bank_name") or payload.get("recipient_bank_code")
        return bool(str(recipient_account or "").strip() and str(recipient_bank or "").strip())
    if task_type in ("airtime", "data"):
        phone = (
            payload.get("phone_number")
            or payload.get("recipient_phone")
            or payload.get("recipientPhone")
            or payload.get("phone")
            or payload.get("target_phone")
        )
        network = payload.get("network")
        return bool(str(phone or "").strip() and str(network or "").strip())
    return False


def _recipient_review_is_confirmed(payload: dict[str, Any]) -> bool:
    if not payload.get("recipient_review_required"):
        return True
    signature = recipient_review_signature(payload)
    return bool(
        signature
        and payload.get("recipient_review_confirmed")
        and payload.get("recipient_review_signature") == signature
    )


def _recipient_ready_for_funding(task_type: str, payload: dict[str, Any]) -> bool:
    if not _has_recipient_destination(payload, task_type):
        return False
    if task_type in ("airtime", "data"):
        return True
    if not _recipient_review_is_confirmed(payload):
        return False
    if payload.get("beneficiary_id") or payload.get("resolved_from_saved_beneficiary"):
        return True
    if str(payload.get("recipient_resolution_provider") or "").strip():
        return True
    if str(payload.get("recipient_resolved_name") or "").strip():
        return True
    return False


def _task_payload(task: Any) -> dict[str, Any]:
    payload = getattr(task, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _is_non_terminal_money_moving_task(task: Any) -> bool:
    return bool(task and task.type in ("transfer", "airtime", "data") and task.stage not in TERMINAL_STAGES)


def _ready_for_batch_funding(task: Any) -> bool:
    if not _is_non_terminal_money_moving_task(task):
        return False
    if task.stage not in _BATCH_FUNDING_MUTABLE_STAGES:
        return False
    payload = _task_payload(task)
    parsed_amount = to_naira(payload.get("amount"))
    return bool(_recipient_ready_for_funding(task.type, payload) and parsed_amount is not None and parsed_amount > 0)


def _task_async_group_id(task: Any) -> str | None:
    value = _task_payload(task).get("async_group_id")
    text = str(value).strip() if value else ""
    return text or None


def _batch_money_moving_tasks_for_wave(
    state: OrchestratorState,
    current_wave: list[str],
    *,
    task_id: str | None = None,
    include_single: bool = False,
    task_types: Collection[str] | None = None,
) -> list[tuple[str, Any]]:
    money_tasks = [
        (candidate_id, task)
        for candidate_id, task in existing_tasks(state, current_wave)
        if _is_non_terminal_money_moving_task(task)
        and (task_types is None or task.type in task_types)
    ]
    if not money_tasks or (len(money_tasks) < 2 and not include_single):
        return []

    if task_id is None:
        return money_tasks

    current_task = get_task(state, task_id)
    if not _is_non_terminal_money_moving_task(current_task):
        return []

    group_id = _task_async_group_id(current_task)
    if group_id:
        grouped_tasks = [
            (candidate_id, task) for candidate_id, task in money_tasks if _task_async_group_id(task) == group_id
        ]
        return grouped_tasks if len(grouped_tasks) >= 2 else []

    current_payload = _task_payload(current_task)
    group_kind = current_payload.get("async_group_kind")
    group_size = to_naira(current_payload.get("async_group_size"))
    if group_kind == "multi_transfer" or (group_size is not None and group_size > 1):
        return money_tasks

    return money_tasks


def _batch_money_moving_task_ids_for_wave(
    state: OrchestratorState,
    current_wave: list[str],
    *,
    task_id: str | None = None,
    task_types: Collection[str] | None = None,
) -> list[str]:
    return [
        candidate_id
        for candidate_id, _task in _batch_money_moving_tasks_for_wave(
            state,
            current_wave,
            task_id=task_id,
            task_types=task_types,
        )
    ]


def _batch_money_moving_tasks_not_ready_for_funding(
    state: OrchestratorState,
    current_wave: list[str],
    *,
    task_id: str | None = None,
    include_single: bool = False,
    task_types: Collection[str] | None = None,
) -> list[str]:
    return [
        candidate_id
        for candidate_id, task in _batch_money_moving_tasks_for_wave(
            state,
            current_wave,
            task_id=task_id,
            include_single=include_single,
            task_types=task_types,
        )
        if not _ready_for_batch_funding(task)
    ]


def _is_plannable_money_moving_task(task: Any, *, allow_existing_funding_plan: bool = False) -> bool:
    if not task or task.type not in ("transfer", "airtime", "data") or task.stage in TERMINAL_STAGES:
        return False
    if task.stage not in _BATCH_FUNDING_MUTABLE_STAGES:
        return False
    payload = task.payload if isinstance(task.payload, dict) else {}
    amount = payload.get("amount")
    parsed_amount = to_naira(amount)
    funding_plan = payload.get("funding_plan")
    existing_plan_needs_recoordination = (
        isinstance(funding_plan, dict) and funding_plan.get("old_single_transfer_plan") is True
    )
    return (
        _recipient_ready_for_funding(task.type, payload)
        and (not funding_plan or (allow_existing_funding_plan and existing_plan_needs_recoordination))
        and parsed_amount is not None
        and parsed_amount > 0
    )


__all__ = [
    "_batch_money_moving_task_ids_for_wave",
    "_batch_money_moving_tasks_for_wave",
    "_batch_money_moving_tasks_not_ready_for_funding",
    "_build_transfer_demand",
    "_is_plannable_money_moving_task",
]
