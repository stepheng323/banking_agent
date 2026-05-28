"""Public scoped task targeting helpers for confirmation edit actions."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_target_resolution import (
    _decision_target_texts,
    _target_task_ids_from_decision,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_target_tasks import (
    _removed_task_from_entry,
)


def confirmation_scoped_task_removal_ids(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
) -> list[str]:
    if getattr(interrupt, "kind", None) != "confirmation":
        return []

    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    if len(task_ids) < 2:
        return []

    matched_task_ids = _target_task_ids_from_decision(
        decision=decision,
        task_ids=task_ids,
        state=state,
    )
    if not matched_task_ids:
        return []
    if len(matched_task_ids) >= len(task_ids) and not getattr(decision, "target_types", None):
        return []
    return matched_task_ids


def confirmation_scoped_task_restore_ids(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
) -> list[str]:
    if getattr(interrupt, "kind", None) != "confirmation":
        return []

    removed = state.removed_confirmation_tasks or {}
    if not removed:
        return []

    removed_task_ids = [str(task_id) for task_id in removed]
    matched_task_ids = _target_task_ids_from_decision(
        decision=decision,
        task_ids=removed_task_ids,
        state=state,
        removed=True,
    )
    if matched_task_ids:
        return matched_task_ids
    if len(removed) == 1 and not getattr(decision, "target_types", None) and not _decision_target_texts(decision):
        return [next(iter(removed.keys()))]
    return []


__all__ = [
    "_removed_task_from_entry",
    "confirmation_scoped_task_removal_ids",
    "confirmation_scoped_task_restore_ids",
]
