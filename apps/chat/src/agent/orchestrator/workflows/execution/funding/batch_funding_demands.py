from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import recipient_review_signature
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import existing_tasks, get_task
from banking.transfers.funding.batch_models import SourceAffinity, TransferDemand
from shared.money import MoneyAmount, require_naira, to_naira


def _build_transfer_demand(task_id: str, task_payload: dict[str, Any]) -> TransferDemand:
    mode = "explicit" if task_payload.get("source_affinity_mode") == "explicit" else "auto"
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
    return TransferDemand(
        task_id=task_id,
        amount=to_naira(task_payload.get("amount")) or require_naira(0),
        source_affinity=SourceAffinity(mode=cast(Any, mode)),
        explicit_sources=explicit_sources,
        explicit_split=explicit_split,
        use_dual_accounts=bool(task_payload.get("use_dual_accounts")),
        preferred_account_id=str(preferred_account_id) if preferred_account_id else None,
        source_pooling_locked=bool(task_payload.get("source_pooling_locked")),
    )


_BATCH_FUNDING_MUTABLE_STAGES = {
    TaskStage.DRAFT,
    TaskStage.EXTRACTED,
    TaskStage.RESOLVED,
    TaskStage.VALIDATED,
    TaskStage.AWAITING_FUNDING_ADJUSTMENT,
    TaskStage.AWAITING_CONFIRMATION,
}


def _has_recipient_destination(payload: dict[str, Any]) -> bool:
    recipient_account = payload.get("recipient_account") or payload.get("recipient_account_number")
    recipient_bank = payload.get("recipient_bank_name") or payload.get("recipient_bank_code")
    return bool(str(recipient_account or "").strip() and str(recipient_bank or "").strip())


def _recipient_review_is_confirmed(payload: dict[str, Any]) -> bool:
    if not payload.get("recipient_review_required"):
        return True
    signature = recipient_review_signature(payload)
    return bool(
        signature
        and payload.get("recipient_review_confirmed")
        and payload.get("recipient_review_signature") == signature
    )


def _recipient_ready_for_funding(payload: dict[str, Any]) -> bool:
    if not _has_recipient_destination(payload):
        return False
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


def _is_non_terminal_transfer_task(task: Any) -> bool:
    return bool(task and task.type == "transfer" and task.stage not in TERMINAL_STAGES)


def _ready_for_batch_funding(task: Any) -> bool:
    if not _is_non_terminal_transfer_task(task):
        return False
    if task.stage not in _BATCH_FUNDING_MUTABLE_STAGES:
        return False
    payload = _task_payload(task)
    parsed_amount = to_naira(payload.get("amount"))
    return bool(
        _recipient_ready_for_funding(payload)
        and parsed_amount is not None
        and parsed_amount > 0
    )


def _task_async_group_id(task: Any) -> str | None:
    value = _task_payload(task).get("async_group_id")
    text = str(value).strip() if value else ""
    return text or None


def _batch_transfer_tasks_for_wave(
    state: OrchestratorState,
    current_wave: list[str],
    *,
    task_id: str | None = None,
) -> list[tuple[str, Any]]:
    transfer_tasks = [
        (candidate_id, task)
        for candidate_id, task in existing_tasks(state, current_wave)
        if _is_non_terminal_transfer_task(task)
    ]
    if len(transfer_tasks) < 2:
        return []

    if task_id is None:
        return transfer_tasks

    current_task = get_task(state, task_id)
    if not _is_non_terminal_transfer_task(current_task):
        return []

    group_id = _task_async_group_id(current_task)
    if group_id:
        grouped_tasks = [
            (candidate_id, task)
            for candidate_id, task in transfer_tasks
            if _task_async_group_id(task) == group_id
        ]
        return grouped_tasks if len(grouped_tasks) >= 2 else []

    current_payload = _task_payload(current_task)
    group_kind = current_payload.get("async_group_kind")
    group_size = to_naira(current_payload.get("async_group_size"))
    if group_kind == "multi_transfer" or (group_size is not None and group_size > 1):
        return transfer_tasks

    return transfer_tasks


def _batch_transfer_task_ids_for_wave(
    state: OrchestratorState,
    current_wave: list[str],
    *,
    task_id: str | None = None,
) -> list[str]:
    return [
        candidate_id
        for candidate_id, _task in _batch_transfer_tasks_for_wave(
            state,
            current_wave,
            task_id=task_id,
        )
    ]


def _batch_transfer_tasks_not_ready_for_funding(
    state: OrchestratorState,
    current_wave: list[str],
    *,
    task_id: str | None = None,
) -> list[str]:
    return [
        candidate_id
        for candidate_id, task in _batch_transfer_tasks_for_wave(
            state,
            current_wave,
            task_id=task_id,
        )
        if not _ready_for_batch_funding(task)
    ]


def _is_plannable_transfer_task(task: Any, *, allow_existing_funding_plan: bool = False) -> bool:
    if not task or task.type != "transfer" or task.stage in TERMINAL_STAGES:
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
        _recipient_ready_for_funding(payload)
        and (not funding_plan or (allow_existing_funding_plan and existing_plan_needs_recoordination))
        and parsed_amount is not None
        and parsed_amount > 0
    )


__all__ = [
    "_batch_transfer_task_ids_for_wave",
    "_batch_transfer_tasks_for_wave",
    "_batch_transfer_tasks_not_ready_for_funding",
    "_build_transfer_demand",
    "_is_plannable_transfer_task",
]
