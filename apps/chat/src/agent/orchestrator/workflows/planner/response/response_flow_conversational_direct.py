from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_grounding import conversation_display_name
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_intents import (
    SOCIAL_META_INTENT,
    SOCIAL_META_RENDER_PARAMS_CTX,
    SOCIAL_META_RESPONSE_KEY_CTX,
    SOCIAL_META_RESPONSE_KEYS,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_locale import _render_greeting
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import (
    _build_bounded_conversational_reply,
    _localized_planner_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_logging import _log_unexpected_turn_route
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.presentation.i18n.message_keys import is_message_key
from banking.presentation.i18n.renderer import render_message
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _direct_planner_response(
    *,
    state: OrchestratorState,
    planner_output: PlannerOutput,
    conversational_locale: str,
    conversational_locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    route_logger: Any | None,
) -> dict[str, Any]:
    logger.info("planner_direct_response_used", locale=conversational_locale)
    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="direct_response",
        route_reason="planner_conversational_response",
        policy_blocked=False,
        fallback_path="planner_non_task",
        route_logger=route_logger,
    )
    return {
        "final_response": _localized_planner_response(planner_output.response, conversational_locale),
        **conversational_locale_updates,
        **context_read_updates,
    }


async def _response_key_render_response(
    *,
    state: OrchestratorState,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    text: str,
    response_key: str,
    conversational_locale: str,
    conversational_locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    conversation_responder: ConversationResponder | None,
    route_logger: Any | None,
) -> dict[str, Any]:
    logger.info("planner_response_key_used", key=response_key, locale=conversational_locale)
    render_response_key = response_key
    render_params: dict[str, object] = {}
    if response_key == "conversational.greeting" and state_view.has_no_active_flow:
        display_name = conversation_display_name(state_view.loaded_context_or_empty)
        if display_name:
            render_response_key = "conversational.greeting_named"
            render_params["display_name"] = display_name
    if response_key in SOCIAL_META_RESPONSE_KEYS:
        responder_reply = await _build_bounded_conversational_reply(
            state_view=state_view,
            text=text,
            locale=conversational_locale,
            conversation_responder=conversation_responder,
            intent=SOCIAL_META_INTENT,
            extra_user_ctx={
                SOCIAL_META_RESPONSE_KEY_CTX: render_response_key,
                SOCIAL_META_RENDER_PARAMS_CTX: render_params,
            },
        )
        if responder_reply:
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="conversation_responder",
                route_reason=f"response_key:{response_key}",
                policy_blocked=False,
                fallback_path="planner_non_task",
                route_logger=route_logger,
            )
            return {
                "final_response": responder_reply,
                **conversational_locale_updates,
                **context_read_updates,
            }
    if render_response_key == "conversational.greeting":
        return {
            "final_response": _render_greeting(conversational_locale),
            **conversational_locale_updates,
            **context_read_updates,
        }
    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="response_key_render",
        route_reason=f"response_key:{response_key}",
        policy_blocked=response_key == "conversational.out_of_scope",
        fallback_path="planner_non_task",
        route_logger=route_logger,
    )
    message_key = render_response_key if is_message_key(render_response_key) else "response.fallback.generic"
    return {
        "final_response": render_message(message_key, conversational_locale, render_params),
        **conversational_locale_updates,
        **context_read_updates,
    }


__all__ = ["_direct_planner_response", "_response_key_render_response"]
