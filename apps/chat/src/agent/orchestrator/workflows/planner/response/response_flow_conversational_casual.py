from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import (
    _build_bounded_conversational_reply,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_logging import _log_unexpected_turn_route
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.presentation.i18n.renderer import render_message


async def _casual_chat_response(
    *,
    state: OrchestratorState,
    state_view: PlannerStateView,
    planner_output: Any,
    text: str,
    conversational_locale: str,
    conversational_locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    conversation_responder: Any | None,
    route_logger: Any | None,
) -> dict[str, Any]:
    responder_reply = await _build_bounded_conversational_reply(
        state_view=state_view,
        text=text,
        locale=conversational_locale,
        conversation_responder=conversation_responder,
    )
    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="conversation_responder",
        route_reason="conversational_casual_chat",
        policy_blocked=False,
        fallback_path="planner_non_task",
        route_logger=route_logger,
    )
    return {
        "final_response": responder_reply or render_message("conversational.out_of_scope", conversational_locale),
        **conversational_locale_updates,
        **context_read_updates,
    }


__all__ = ["_casual_chat_response"]
