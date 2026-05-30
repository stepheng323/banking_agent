from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import (
    _build_bounded_conversational_reply,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_logging import _log_unexpected_turn_route
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _missing_conversational_response_fallback(
    *,
    state: OrchestratorState,
    planner_output: Any,
    text: str,
    conversational_locale: str,
    conversational_locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    conversation_responder: Any | None,
    route_logger: Any | None,
) -> dict[str, Any]:
    logger.info(
        "planner_response_key_missing_and_no_response",
        intent=planner_output.primary_intent,
        detected_language=getattr(planner_output, "detected_language", None),
    )
    responder_reply = await _build_bounded_conversational_reply(
        state=state,
        text=text,
        locale=conversational_locale,
        conversation_responder=conversation_responder,
    )
    if responder_reply:
        _log_unexpected_turn_route(
            state=state,
            planner_output=planner_output,
            selected_route="conversation_responder",
            route_reason="missing_conversational_response_key",
            policy_blocked=False,
            fallback_path="planner_non_task",
            route_logger=route_logger,
        )
        return {
            "final_response": responder_reply,
            **conversational_locale_updates,
            **context_read_updates,
        }

    fallback_key: MessageKey = "conversational.out_of_scope"
    logger.info("conversational_fallback_deterministic_used", key=fallback_key, locale=conversational_locale)
    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="out_of_scope",
        route_reason="missing_conversational_response_key",
        policy_blocked=False,
        fallback_path="planner_non_task",
        route_logger=route_logger,
    )
    return {
        "final_response": render_message(fallback_key, conversational_locale),
        **conversational_locale_updates,
        **context_read_updates,
    }


__all__ = ["_missing_conversational_response_fallback"]
