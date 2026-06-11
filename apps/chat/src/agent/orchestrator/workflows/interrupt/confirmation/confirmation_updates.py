from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import (
    is_explicit_confirmation_approval,
)
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _amount_patch(amount: float) -> dict[str, Any]:
    return {
        "confirmation": {"confirmed": False},
        "amount": amount,
        "transfer_percentage": None,
        "transfer_all": False,
        "funding_plan": None,
        "suggested_funding_plan": None,
        "suggested_amount": None,
    }


def _synth_confirmation_followup_message(task: TaskSpec) -> str | None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    if task.type != "transfer":
        return None

    recipient_name = str(payload.get("recipient_name") or "").strip()
    amount = payload.get("amount")
    narration = str(
        payload.get("authored_narration") or payload.get("narration") or payload.get("user_note") or ""
    ).strip()
    if not recipient_name:
        return None

    amount_text = None
    if isinstance(amount, (int, float)) and float(amount) > 0:
        amount_text = str(int(amount)) if float(amount).is_integer() else str(float(amount))

    base = f"Send {amount_text} to {recipient_name}" if amount_text else f"Send money to {recipient_name}"
    if narration:
        return f"{base} for {narration}"
    return base


def _stash_previous_confirmation_snapshots(state: OrchestratorState, task_ids: list[str]) -> None:
    state_view = interrupt_state_view(state)
    for task_id in task_ids:
        task = state_view.task(task_id)
        if task is None:
            continue
        confirmation = task.payload.get("confirmation")
        snapshot = confirmation.get("snapshot") if isinstance(confirmation, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            task.payload["previous_confirmation_snapshot"] = dict(snapshot)
        else:
            task.payload.pop("previous_confirmation_snapshot", None)


def _is_explicit_confirmation_approval_text(state: OrchestratorState, text: str) -> bool:
    shortcut_locale = interrupt_state_view(state).shortcut_locale
    return is_explicit_confirmation_approval(text=text, locale=shortcut_locale)


def _approve_confirmation_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    new_tasks = state_view.task_map_copy()
    logger.info("confirmation_confirmed", tasks=interrupt.task_ids, via_pin=state_view.pin_verified)
    for tid in interrupt.task_ids:
        task = new_tasks[tid].model_copy(deep=True)
        task.payload.setdefault("confirmation", {})
        task.payload["confirmation"]["confirmed"] = True
        schedule_edit_without_auth = (
            task.type == "schedule"
            and str(task.payload.get("action") or "").strip().lower() == "edit_scheduled_transaction"
            and task.payload.get("schedule_edit_requires_auth") is False
        )
        task.stage = (
            TaskStage.EXECUTING if state_view.pin_verified or schedule_edit_without_auth else TaskStage.AWAITING_AUTH
        )
        new_tasks[tid] = task
    return {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": new_tasks,
    }


__all__ = [
    "_amount_patch",
    "_approve_confirmation_updates",
    "_is_explicit_confirmation_approval_text",
    "_stash_previous_confirmation_snapshots",
    "_synth_confirmation_followup_message",
]
