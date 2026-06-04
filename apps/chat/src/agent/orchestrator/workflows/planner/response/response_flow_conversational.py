"""Conversational planner no-task response handling."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import (
    _resolved_locale_with_precedence,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_conversational_special import (
    _build_banking_ambiguity_response,
    _build_contextual_casual_followup_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_conversational_standard import (
    _build_standard_conversational_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView


async def _build_conversational_non_task_response(
    *,
    state: OrchestratorState,
    state_view: PlannerStateView,
    planner_output: Any,
    text: str,
    redis_client: Any | None,
    current_locale: str,
    detected_locale: str | None,
    locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    conversation_responder: Any | None,
    route_logger: Any | None = None,
) -> dict[str, Any]:
    conversational_locale, conversational_locale_updates = await _resolved_locale_with_precedence(
        state_view=state_view,
        current_locale=current_locale,
        detected_locale=detected_locale,
        redis_client=redis_client,
        locale_updates=locale_updates,
    )
    special_response = await _build_banking_ambiguity_response(
        state=state,
        state_view=state_view,
        planner_output=planner_output,
        text=text,
        conversational_locale=conversational_locale,
        conversational_locale_updates=conversational_locale_updates,
        context_read_updates=context_read_updates,
        route_logger=route_logger,
    )
    if special_response is not None:
        return special_response

    special_response = await _build_contextual_casual_followup_response(
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
    if special_response is not None:
        return special_response

    return await _build_standard_conversational_response(
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


__all__ = ["_build_conversational_non_task_response"]
