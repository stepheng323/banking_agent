"""Route-changing operation handlers for semantic pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _cancel_updates, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import _handle_switch_intent_route
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import KNOWN_SWITCH_INTENTS, TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_flow import _status_query_updates
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from shared.types.planner import InterruptRouteDecision

from ..flow.pending_action_confirmation_flow import _confirmation_edit_clarification_updates
from ..payloads.pending_action_payload_account_switch import _account_switch_source_overrides


async def _resolve_status_query_operation(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
    text: str,
    task_planner: Any,
    active_type: str,
    current_task_types: set[str],
    services: OrchestrationServices,
    redis_client: Any | None,
) -> dict[str, Any]:
    route = InterruptRouteDecision(
        decision="status_query",
        confidence=decision.confidence,
        detected_language=decision.detected_language,
        status_query_type=decision.status_query_type or "recap",
        reason=decision.reason or "pending_action_status_query",
    )
    return await _status_query_updates(
        state=state,
        interrupt=interrupt,
        route=route,
        current_task_types=current_task_types,
        semantic_path_shape="pending_action_edit",
        task_planner=task_planner,
        text=text,
        active_type=active_type,
        services=services,
        redis_client=redis_client,
    )


async def _resolve_switch_intent_operation(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
    text: str,
    task_planner: Any,
    active_type: str,
    current_task_types: set[str],
    services: OrchestrationServices,
) -> dict[str, Any] | None:
    target_intent = str(decision.target_intent or "").strip().lower()
    if target_intent not in KNOWN_SWITCH_INTENTS:
        return None

    if target_intent == "account" and current_task_types.intersection(TRANSACTION_INTENTS):
        overrides = _account_switch_source_overrides(
            state=state,
            interrupt=interrupt,
            decision=decision,
            text=text,
        )
        if overrides:
            logger.info(
                "pending_action_account_switch_as_source_edit",
                task_ids=list(overrides.keys()),
            )
            return _continue_flow_updates(
                state,
                interrupt,
                precomputed_payload_overrides=overrides,
            )
        logger.info(
            "pending_action_account_switch_blocked",
            reason="transaction_confirmation_active",
            task_ids=getattr(interrupt, "task_ids", None),
        )
        return _confirmation_edit_clarification_updates(state, interrupt)

    route = InterruptRouteDecision(
        decision="switch_intent",
        confidence=decision.confidence,
        detected_language=decision.detected_language,
        target_intent=target_intent,
        target_mode="continuation" if target_intent in TRANSACTION_INTENTS else "new",
        reason=decision.reason or "pending_action_switch_intent",
    )
    return await _handle_switch_intent_route(
        state=state,
        interrupt=interrupt,
        route=route,
        task_planner=task_planner,
        text=text,
        active_type=active_type,
        current_task_types=current_task_types,
        services=services,
    )


async def _resolve_cancel_all_operation(
    *,
    state: OrchestratorState,
    interrupt: Any,
    redis_client: Any | None,
) -> dict[str, Any]:
    return await _cancel_updates(state, interrupt, redis_client)


__all__ = [
    "_resolve_cancel_all_operation",
    "_resolve_status_query_operation",
    "_resolve_switch_intent_operation",
]
