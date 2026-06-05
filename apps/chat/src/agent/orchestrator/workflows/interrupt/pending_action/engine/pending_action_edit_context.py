from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.engine.pending_action_edit_types import (
    PENDING_ACTION_EDIT_TASK_TYPES,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view


def _task_amount(payload: dict[str, Any]) -> Any:
    return payload.get("amount") or payload.get("suggested_amount")


def _task_label(task: TaskSpec) -> str:
    payload = task.payload if isinstance(task.payload, dict) else {}
    if task.type == "transfer":
        recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name") or payload.get("recipient")
        bank = payload.get("recipient_bank_name") or payload.get("bank_name")
        account = payload.get("recipient_account") or payload.get("recipient_account_number")
        return " ".join(str(part) for part in (recipient, bank, account) if part)
    if task.type in {"airtime", "data"}:
        phone = payload.get("phone") or payload.get("recipient_phone") or payload.get("target_phone")
        network = payload.get("network")
        plan = payload.get("plan") or payload.get("plan_name")
        return " ".join(str(part) for part in (task.type, phone, network, plan) if part)
    return task.type


def _task_context_entry(task_id: str, task: TaskSpec) -> dict[str, Any]:
    payload = task.payload if isinstance(task.payload, dict) else {}
    entry = {
        "task_id": task_id,
        "type": task.type,
        "stage": task.stage.value if hasattr(task.stage, "value") else str(task.stage),
        "label": _task_label(task),
        "amount": _task_amount(payload),
        "recipient_name": payload.get("recipient_name"),
        "recipient_resolved_name": payload.get("recipient_resolved_name"),
        "recipient_account": payload.get("recipient_account") or payload.get("recipient_account_number"),
        "recipient_bank_name": payload.get("recipient_bank_name") or payload.get("bank_name"),
        "phone": payload.get("phone") or payload.get("recipient_phone") or payload.get("target_phone"),
        "network": payload.get("network"),
        "is_self": payload.get("is_self"),
        "plan": payload.get("plan") or payload.get("plan_name"),
        "plan_code": payload.get("plan_code"),
        "size_preference": payload.get("size_preference") or payload.get("plan_size_gb"),
        "validity_preference": payload.get("validity_preference") or payload.get("plan_validity_days"),
        "selection_preference": payload.get("selection_preference"),
        "usage_intent": payload.get("usage_intent"),
        "source_bank_name": payload.get("source_bank_name"),
        "source_account_number": payload.get("source_account_number"),
        "narration": payload.get("narration") or payload.get("authored_narration") or payload.get("user_note"),
    }
    return {key: value for key, value in entry.items() if value not in (None, "", [])}


def _removed_task_from_entry(entry: Any) -> TaskSpec | None:
    task = entry.get("task") if isinstance(entry, dict) else None
    if isinstance(task, dict):
        task = TaskSpec.model_validate(task)
    return task if isinstance(task, TaskSpec) else None


def build_pending_action_edit_context(state: OrchestratorState, interrupt: Any) -> str:
    """Build compact context for semantic pending-action edit classification."""
    state_view = interrupt_state_view(state)
    active_task_ids = state_view.active_task_ids_for_interrupt(interrupt)
    active_entries = [
        _task_context_entry(task_id, task)
        for task_id in active_task_ids
        if (task := state_view.task(task_id)) is not None and task.type in PENDING_ACTION_EDIT_TASK_TYPES
    ]
    removed_entries = []
    for task_id, entry in (state_view.removed_confirmation_tasks or {}).items():
        task = _removed_task_from_entry(entry)
        if task is None or task.type not in PENDING_ACTION_EDIT_TASK_TYPES:
            continue
        removed_entries.append(_task_context_entry(str(task_id), task))

    lines = ["Active pending tasks:"]
    if active_entries:
        for entry in active_entries:
            lines.append(str(entry))
    else:
        lines.append("None")

    lines.append("Removed tasks available for restore:")
    if removed_entries:
        for entry in removed_entries:
            lines.append(str(entry))
    else:
        lines.append("None")
    return "\n".join(lines)


__all__ = [
    "_removed_task_from_entry",
    "_task_amount",
    "_task_context_entry",
    "_task_label",
    "build_pending_action_edit_context",
]
