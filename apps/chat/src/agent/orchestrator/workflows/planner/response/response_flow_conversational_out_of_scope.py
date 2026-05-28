from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.presentation.conversational_style import format_out_of_scope_reply
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import (
    _build_bounded_conversational_reply,
    _localized_planner_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_logging import _log_unexpected_turn_route
from shared.services.conversation_responder_text import is_banking_refusal_reply


async def _out_of_scope_response(
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
    responder_reply = None
    if not planner_output.response or is_banking_refusal_reply(
        planner_output.response,
        locale=conversational_locale,
    ):
        responder_reply = await _build_bounded_conversational_reply(
            state=state,
            text=text,
            locale=conversational_locale,
            conversation_responder=conversation_responder,
        )
    empathy_source = (
        _localized_planner_response(planner_output.response, conversational_locale) if planner_output.response else None
    )
    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="out_of_scope",
        route_reason="conversational_out_of_scope",
        policy_blocked=True,
        fallback_path="planner_non_task",
        route_logger=route_logger,
    )
    return {
        "final_response": responder_reply or format_out_of_scope_reply(conversational_locale, empathy_source),
        **conversational_locale_updates,
        **context_read_updates,
    }


__all__ = ["_out_of_scope_response"]
