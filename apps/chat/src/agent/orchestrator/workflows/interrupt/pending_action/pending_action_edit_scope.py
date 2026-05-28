from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _is_supported_input_edit_interrupt(state: OrchestratorState, interrupt: Any) -> bool:
    """Allow semantic edits for data input prompts that are really plan-choice moments."""
    if getattr(interrupt, "kind", None) != "input":
        return False
    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    if not isinstance(fields_by_task, dict):
        return False
    active_task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if len(active_task_ids) != 1:
        return False
    task_id = active_task_ids[0]
    task = state.tasks.get(task_id)
    if task is None or task.type != "data":
        return False
    required_fields = {str(field) for field in fields_by_task.get(task_id, []) if isinstance(field, str)}
    return bool(
        required_fields
        & {
            "data_plan_id",
            "data_plan_preference",
            "target_phone",
            "phone",
            "recipient_phone",
            "network",
        }
    )


__all__ = ["_is_supported_input_edit_interrupt"]
