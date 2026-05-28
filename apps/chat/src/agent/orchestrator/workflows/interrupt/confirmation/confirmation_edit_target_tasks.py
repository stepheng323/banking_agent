"""Task lookup helpers for scoped confirmation edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _removed_task_from_entry(entry: Any) -> TaskSpec | None:
    task = entry.get("task") if isinstance(entry, dict) else None
    if isinstance(task, dict):
        task = TaskSpec.model_validate(task)
    return task if isinstance(task, TaskSpec) else None


def _task_for_target_id(state: OrchestratorState, task_id: str, *, removed: bool) -> TaskSpec | None:
    if not removed:
        return state.tasks.get(task_id)
    return _removed_task_from_entry((state.removed_confirmation_tasks or {}).get(task_id))


__all__ = ["_removed_task_from_entry", "_task_for_target_id"]
