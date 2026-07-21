"""Auth prompt headers for execution confirmation gates."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import get_task, task_types_for_ids
from banking.presentation.formatters.auth_reason import format_auth_reason


def _auth_header_for_tasks(state: OrchestratorState, task_ids: list[str], *, locale: str) -> str:
    for task_id in task_ids:
        task = get_task(state, task_id)
        payload = task.payload if task is not None and isinstance(task.payload, dict) else {}
        if (
            str(payload.get("action") or "").strip().lower()
            in {"edit_scheduled_transaction", "resume_scheduled_transaction"}
            and payload.get("schedule_edit_requires_auth") is True
        ):
            return "Authorize Schedule Update"
    task_types = task_types_for_ids(state, task_ids)
    if len(task_types) == 1:
        return format_auth_reason(next(iter(task_types)), locale=locale)
    return format_auth_reason("mixed", locale=locale)


__all__ = ["_auth_header_for_tasks"]
