"""Semantic pending-action edit resolution."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_restore import (
    restore_confirmation_tasks_and_reconfirm_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_targets import (
    confirmation_scoped_task_removal_ids,
    confirmation_scoped_task_restore_ids,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_add_operation import (
    _resolve_add_task_operation,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_confirmation_flow import (
    _confirmation_edit_clarification_updates,
    _remove_or_cancel_confirmation_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_edit_engine import (
    PendingActionEditEngine,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_field_operation import (
    _resolve_field_update_operation,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_route_operations import (
    _resolve_cancel_all_operation,
    _resolve_status_query_operation,
    _resolve_switch_intent_operation,
)
from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner


async def _resolve_semantic_pending_action_edit_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    task_planner: TaskPlanner | None,
    active_type: str,
    current_task_types: set[str],
    services: dict[str, Any],
    redis_client: Any | None,
) -> dict[str, Any] | None:
    resolution = await PendingActionEditEngine().interpret(
        state=state,
        interrupt=interrupt,
        text=text,
        task_planner=task_planner,
    )
    if resolution is None:
        return None

    decision = resolution.decision
    logger.info(
        "pending_action_edit_decision",
        operation=decision.operation,
        confidence=decision.confidence,
        target_task_ids=decision.target_task_ids,
        target_types=decision.target_types,
        target_texts=decision.target_texts,
    )

    if decision.operation == "remove_tasks":
        task_ids = confirmation_scoped_task_removal_ids(
            state=state,
            interrupt=interrupt,
            decision=decision,
        )
        if task_ids:
            return await _remove_or_cancel_confirmation_tasks(
                state=state,
                interrupt=interrupt,
                redis_client=redis_client,
                task_ids_to_remove=task_ids,
            )

    if decision.operation == "restore_tasks":
        task_ids = confirmation_scoped_task_restore_ids(
            state=state,
            interrupt=interrupt,
            decision=decision,
        )
        if task_ids:
            return restore_confirmation_tasks_and_reconfirm_updates(
                state=state,
                interrupt=interrupt,
                task_ids_to_restore=task_ids,
            )

    if decision.operation in {"update_fields", "show_options"}:
        return _resolve_field_update_operation(
            state=state,
            interrupt=interrupt,
            decision=decision,
        )

    if decision.operation == "add_tasks":
        return await _resolve_add_task_operation(
            state=state,
            interrupt=interrupt,
            decision=decision,
            text=text,
            task_planner=task_planner,
            active_type=active_type,
            current_task_types=current_task_types,
            services=services,
        )

    if decision.operation == "cancel_all":
        return await _resolve_cancel_all_operation(
            state=state,
            interrupt=interrupt,
            redis_client=redis_client,
        )

    if decision.operation == "status_query":
        return await _resolve_status_query_operation(
            state=state,
            interrupt=interrupt,
            decision=decision,
            text=text,
            task_planner=task_planner,
            active_type=active_type,
            current_task_types=current_task_types,
            services=services,
            redis_client=redis_client,
        )

    if decision.operation == "switch_intent":
        return await _resolve_switch_intent_operation(
            state=state,
            interrupt=interrupt,
            decision=decision,
            text=text,
            task_planner=task_planner,
            active_type=active_type,
            current_task_types=current_task_types,
            services=services,
        )

    if decision.operation == "unclear":
        return _confirmation_edit_clarification_updates(state, interrupt)

    return None


__all__ = [
    "_resolve_semantic_pending_action_edit_updates",
]
