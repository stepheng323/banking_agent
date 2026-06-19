"""Planner no-task and cancellation response flow helpers."""

from typing import Any

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    get_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary, OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_locale import _detected_locale_value
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_cancellation import (
    _build_cancellation_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import (
    _build_bounded_conversational_reply,
    _localized_planner_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_conversational import (
    _build_conversational_non_task_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_logging import _log_unexpected_turn_route
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.presentation.i18n.bridge import render_safe_capability_fallback
from banking.presentation.i18n.renderer import render_message
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _build_non_task_response(
    *,
    state: OrchestratorState,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    text: str,
    redis_client: redis.Redis | None,
    active_intent: str | None,
    current_locale: str,
    locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    conversation_responder: ConversationResponder | None = None,
) -> dict[str, Any] | None:
    detected_locale = _detected_locale_value(planner_output)

    cancellation_response = await _build_cancellation_response(
        state=state,
        state_view=state_view,
        planner_output=planner_output,
        redis_client=redis_client,
        current_locale=current_locale,
        detected_locale=detected_locale,
        locale_updates=locale_updates,
        context_read_updates=context_read_updates,
    )
    if cancellation_response is not None:
        return cancellation_response

    if planner_output and getattr(planner_output, "unsupported_capability", None) is not None:


        unsupported_cap = planner_output.unsupported_capability
        capability = get_unsupported_capability(unsupported_cap)
        if capability is not None:
            params = unsupported_capability_params(capability, locale=current_locale)
            logger.info("planner_non_task_unsupported_capability", capability_key=capability.key)
            return {
                "capability_boundary": CapabilityBoundary(key=capability.key, label=capability.label),
                "final_response": render_message(
                    "capability.unsupported_unavailable",
                    current_locale,
                    params,
                ),
                **locale_updates,
                **context_read_updates,
            }

    if planner_output and planner_output.tasks:
        return None

    if planner_output and planner_output.primary_intent == "conversational":
        return await _build_conversational_non_task_response(
            state=state,
            state_view=state_view,
            planner_output=planner_output,
            text=text,
            redis_client=redis_client,
            current_locale=current_locale,
            detected_locale=detected_locale,
            locale_updates=locale_updates,
            context_read_updates=context_read_updates,
            conversation_responder=conversation_responder,
            route_logger=logger,
        )

    if state_view.has_waves and planner_output and planner_output.primary_intent != "conversational":
        if planner_output.primary_intent != active_intent:
            logger.info("planner_switch_empty_tasks", old=active_intent, new=planner_output.primary_intent)
            return {
                "waves": [],
                "final_response": _localized_planner_response(planner_output.response, current_locale),
                **locale_updates,
                **context_read_updates,
            }

    if planner_output and planner_output.response:
        _log_unexpected_turn_route(
            state=state,
            planner_output=planner_output,
            selected_route="direct_response",
            route_reason="planner_non_conversational_response",
            policy_blocked=False,
            fallback_path="planner_non_task",
            route_logger=logger,
        )
        return {
            "final_response": _localized_planner_response(planner_output.response, current_locale),
            **locale_updates,
            **context_read_updates,
        }
    if not state_view.has_session_stack and state_view.pending_interrupt is None:
        responder_reply = await _build_bounded_conversational_reply(
            state_view=state_view,
            text=text,
            locale=current_locale,
            conversation_responder=conversation_responder,
        )
        if responder_reply:
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="conversation_responder",
                route_reason="no_tasks_no_response",
                policy_blocked=False,
                fallback_path="planner_non_task",
                route_logger=logger,
            )
            return {
                "final_response": responder_reply,
                **locale_updates,
                **context_read_updates,
            }
    if state_view.pending_interrupt is not None:
        _log_unexpected_turn_route(
            state=state,
            planner_output=planner_output,
            selected_route="interrupt_reprompt",
            route_reason="no_tasks_no_response_pending_interrupt",
            policy_blocked=False,
            fallback_path="planner_non_task",
            route_logger=logger,
        )
        return {
            **_reprompt_updates(state, state_view.pending_interrupt),
            **context_read_updates,
        }
    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="capability_fallback",
        route_reason="no_tasks_no_response",
        policy_blocked=False,
        fallback_path="planner_non_task",
        route_logger=logger,
    )
    return {
        "final_response": render_safe_capability_fallback(current_locale),
        **locale_updates,
        **context_read_updates,
    }


__all__ = ["_build_non_task_response", "_log_unexpected_turn_route"]
