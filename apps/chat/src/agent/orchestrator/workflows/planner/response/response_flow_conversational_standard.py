from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_conversational_casual import (
    _casual_chat_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_conversational_direct import (
    _direct_planner_response,
    _response_key_render_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_conversational_missing import (
    _missing_conversational_response_fallback,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_conversational_out_of_scope import (
    _out_of_scope_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView


async def _build_standard_conversational_response(
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
    response_key = planner_output.response_key
    if response_key == "conversational.casual_chat":
        return await _casual_chat_response(
            state=state,
            state_view=state_view,
            planner_output=planner_output,
            text=text,
            conversational_locale=conversational_locale,
            conversational_locale_updates=conversational_locale_updates,
            context_read_updates=context_read_updates,
            conversation_responder=conversation_responder,
            route_logger=route_logger,
        )
    if response_key == "conversational.out_of_scope":
        return await _out_of_scope_response(
            state=state,
            state_view=state_view,
            planner_output=planner_output,
            text=text,
            conversational_locale=conversational_locale,
            conversational_locale_updates=conversational_locale_updates,
            context_read_updates=context_read_updates,
            conversation_responder=conversation_responder,
            route_logger=route_logger,
        )
    if planner_output.response:
        return _direct_planner_response(
            state=state,
            planner_output=planner_output,
            conversational_locale=conversational_locale,
            conversational_locale_updates=conversational_locale_updates,
            context_read_updates=context_read_updates,
            route_logger=route_logger,
        )
    if response_key:
        return _response_key_render_response(
            state=state,
            planner_output=planner_output,
            response_key=response_key,
            conversational_locale=conversational_locale,
            conversational_locale_updates=conversational_locale_updates,
            context_read_updates=context_read_updates,
            route_logger=route_logger,
        )
    return await _missing_conversational_response_fallback(
        state=state,
        state_view=state_view,
        planner_output=planner_output,
        text=text,
        conversational_locale=conversational_locale,
        conversational_locale_updates=conversational_locale_updates,
        context_read_updates=context_read_updates,
        conversation_responder=conversation_responder,
        route_logger=route_logger,
    )


__all__ = ["_build_standard_conversational_response"]
