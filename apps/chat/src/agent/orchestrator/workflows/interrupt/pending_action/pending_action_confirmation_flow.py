"""Confirmation-specific pending-action helpers."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_remove import (
    remove_confirmation_tasks_and_reconfirm_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_targets import (
    confirmation_scoped_task_restore_ids,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _cancel_updates, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_payload_fields import (
    _pending_edit_has_fields,
)


async def _remove_or_cancel_confirmation_tasks(
    *,
    state: OrchestratorState,
    interrupt: Any,
    redis_client: Any | None,
    task_ids_to_remove: list[str],
) -> dict[str, Any]:
    active_task_ids = {str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks}
    if active_task_ids and set(task_ids_to_remove) >= active_task_ids:
        return await _cancel_updates(state, interrupt, redis_client)
    return remove_confirmation_tasks_and_reconfirm_updates(
        state=state,
        interrupt=interrupt,
        task_ids_to_remove=task_ids_to_remove,
    )


def _restore_fallback_task_ids_for_misclassified_add(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
    text: str,
) -> list[str]:
    """Recover a recently removed task when the semantic model calls it a fresh add."""
    if getattr(interrupt, "kind", None) != "confirmation":
        return []
    removed = state.removed_confirmation_tasks or {}
    if len(removed) != 1:
        return []
    if getattr(decision, "operation", None) != "add_tasks":
        return []
    if _pending_edit_has_fields(decision):
        return []

    restore_text_parts = [
        text,
        getattr(decision, "add_instruction", None),
        getattr(decision, "reason", None),
        *(getattr(decision, "target_texts", None) or []),
    ]
    restore_text = " ".join(str(part).lower() for part in restore_text_parts if part)
    if not any(marker in restore_text for marker in ("back", "restore", "revert", "undo")):
        return []

    matched_task_ids = confirmation_scoped_task_restore_ids(
        state=state,
        interrupt=interrupt,
        decision=decision,
    )
    if matched_task_ids:
        return matched_task_ids
    return [str(next(iter(removed.keys())))]


def _confirmation_edit_clarification_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_clarification import (
        _build_confirmation_scope_clarification_outbox,
    )

    logger.info("pending_action_edit_scope_unresolved", task_ids=getattr(interrupt, "task_ids", None))
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
        "outbox": _build_confirmation_scope_clarification_outbox(state),
    }


__all__ = [
    "_confirmation_edit_clarification_updates",
    "_remove_or_cancel_confirmation_tasks",
    "_restore_fallback_task_ids_for_misclassified_add",
]
