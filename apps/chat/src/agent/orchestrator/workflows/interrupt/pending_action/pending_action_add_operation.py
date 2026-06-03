"""Add-task operation handling for semantic pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_edit_restore import (
    restore_confirmation_tasks_and_reconfirm_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_confirmation_flow import (
    _restore_fallback_task_ids_for_misclassified_add,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import _handle_switch_intent_route
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from shared.types.planner import InterruptRouteDecision


async def _resolve_add_task_operation(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
    text: str,
    task_planner: Any,
    active_type: str,
    current_task_types: set[str],
    services: dict[str, Any],
) -> dict[str, Any] | None:
    restore_task_ids = _restore_fallback_task_ids_for_misclassified_add(
        state=state,
        interrupt=interrupt,
        decision=decision,
        text=text,
    )
    if restore_task_ids:
        return restore_confirmation_tasks_and_reconfirm_updates(
            state=state,
            interrupt=interrupt,
            task_ids_to_restore=restore_task_ids,
        )

    target_types = [
        str(task_type).strip().lower()
        for task_type in (decision.target_types or [])
        if str(task_type).strip().lower() in TRANSACTION_INTENTS
    ]
    # For add-task edits, the concrete transaction type is the safest signal.
    # Some models populate target_intent even though the operation is add_tasks.
    target_intent = target_types[0] if len(set(target_types)) == 1 else (decision.target_intent or "").strip().lower()
    if target_intent not in TRANSACTION_INTENTS:
        return None

    route = InterruptRouteDecision(
        decision="switch_intent",
        confidence=decision.confidence,
        detected_language=decision.detected_language,
        target_intent=target_intent,
        target_mode="continuation",
        reason=decision.reason or "pending_action_add_task",
    )
    return await _handle_switch_intent_route(
        state=state,
        interrupt=interrupt,
        route=route,
        task_planner=task_planner,
        text=decision.add_instruction or text,
        active_type=active_type,
        current_task_types=current_task_types,
        services=services,
    )


__all__ = ["_resolve_add_task_operation"]
