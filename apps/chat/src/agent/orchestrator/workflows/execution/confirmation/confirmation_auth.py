"""Auth prompt headers for execution confirmation gates."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.formatters.auth_reason import format_auth_reason


def _auth_header_for_tasks(state: OrchestratorState, task_ids: list[str], *, locale: str) -> str:
    for task_id in task_ids:
        task = state.tasks.get(task_id)
        payload = task.payload if task is not None and isinstance(task.payload, dict) else {}
        if (
            str(payload.get("action") or "").strip().lower() == "edit_scheduled_transaction"
            and payload.get("schedule_edit_requires_auth") is True
        ):
            return "Authorize Schedule Update"
    task_types = {state.tasks[task_id].type for task_id in task_ids if task_id in state.tasks}
    if len(task_types) == 1:
        return format_auth_reason(next(iter(task_types)), locale=locale)
    return format_auth_reason("mixed", locale=locale)


__all__ = ["_auth_header_for_tasks"]
