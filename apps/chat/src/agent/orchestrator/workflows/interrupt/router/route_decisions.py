from typing import Any

from apps.chat.src.agent.orchestrator.confirmation.confirmation_guardrails import is_safe_guarded_approval_text
from apps.chat.src.agent.orchestrator.confirmation.confirmation_models import APPROVAL_CONFIDENCE_THRESHOLD
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.auth.auth_resolve import _approve_auth_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_updates import (
    _approve_confirmation_updates,
    _is_explicit_confirmation_approval_text,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _cancel_updates, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_reprompt import _reprompt_or_reset_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_payload_account_switch import (
    _account_switch_source_overrides,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import (
    _handle_switch_intent_route,
    _is_same_flow_transactional_switch,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_flow import _status_query_updates
from shared.types.planner import InterruptRouteDecision


async def _apply_interrupt_route_decision(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    task_planner: Any,
    text: str,
    active_type: str,
    services: dict[str, Any],
    redis_client: Any,
) -> dict[str, Any]:
    if route.decision == "status_query":
        return await _status_query_updates(
            state=state,
            interrupt=interrupt,
            route=route,
            current_task_types=current_task_types,
            semantic_path_shape="interrupt_router_only",
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            services=services,
            redis_client=redis_client,
        )

    if route.decision == "cancel":
        return await _cancel_updates(state, interrupt, redis_client)

    if route.decision == "reject_flow":
        return await _cancel_updates(state, interrupt, redis_client)

    if route.decision == "approve_flow":
        if interrupt.kind == "confirmation":
            explicit_approval = _is_explicit_confirmation_approval_text(state, text)
            guarded_llm_approval = (
                route.confidence >= APPROVAL_CONFIDENCE_THRESHOLD
                and is_safe_guarded_approval_text(text, prompt_kind="transaction_confirmation")
            )
            if not (explicit_approval or guarded_llm_approval):
                logger.info(
                    "confirmation_approve_blocked_non_explicit_text",
                    tasks=interrupt.task_ids,
                    confidence=route.confidence,
                )
                return _continue_flow_updates(state, interrupt)
            return _approve_confirmation_updates(state, interrupt)
        if interrupt.kind == "auth":
            return _approve_auth_updates(state, interrupt)
        # Input interrupts cannot be "approved"; keep flow deterministic.
        return await _reprompt_or_reset_updates(state, interrupt, redis_client)

    if route.decision == "continue_flow":
        return _continue_flow_updates(state, interrupt)

    if route.decision == "switch_intent":
        if _is_same_flow_transactional_switch(route=route, interrupt=interrupt, active_type=active_type):
            logger.info(
                "interrupt_same_flow_switch_shortcut",
                kind=interrupt.kind,
                active_type=active_type,
                target_intent=route.target_intent,
            )
            return _continue_flow_updates(state, interrupt)
        if interrupt.kind == "confirmation" and (route.target_intent or "").strip().lower() == "account":
            if current_task_types.intersection(TRANSACTION_INTENTS):
                overrides = _account_switch_source_overrides(
                    state=state,
                    interrupt=interrupt,
                    decision=route,
                    text=text,
                )
                if overrides:
                    logger.info(
                        "interrupt_account_switch_as_source_edit",
                        task_ids=list(overrides.keys()),
                    )
                    return _continue_flow_updates(
                        state,
                        interrupt,
                        precomputed_payload_overrides=overrides,
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

    # "unclear" or any unrecognized decision stays non-destructive.
    return await _reprompt_or_reset_updates(state, interrupt, redis_client)


__all__ = ["_apply_interrupt_route_decision"]
