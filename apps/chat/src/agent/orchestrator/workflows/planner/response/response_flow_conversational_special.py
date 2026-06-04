"""Special-case conversational no-task response routes."""

from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import is_contextual_casual_followup_turn
from apps.chat.src.agent.orchestrator.guardrails.banking_ambiguity import (
    classify_banking_coded_ambiguity,
    render_banking_coded_ambiguity_prompt,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import (
    _build_bounded_conversational_reply,
)
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_logging import _log_unexpected_turn_route
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import PlannerOutput


async def _build_banking_ambiguity_response(
    *,
    state: OrchestratorState,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    text: str,
    conversational_locale: str,
    conversational_locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    route_logger: Any | None,
) -> dict[str, Any] | None:
    ambiguous_banking_domain = classify_banking_coded_ambiguity(text)
    if ambiguous_banking_domain is not None and state_view.has_no_active_flow:
        _log_unexpected_turn_route(
            state=state,
            planner_output=planner_output,
            selected_route="banking_ambiguity_clarify",
            route_reason=f"banking_coded_ambiguity:{ambiguous_banking_domain}",
            policy_blocked=False,
            fallback_path="planner_non_task",
            route_logger=route_logger,
        )
        return {
            "final_response": render_banking_coded_ambiguity_prompt(text, locale=conversational_locale),
            **conversational_locale_updates,
            **context_read_updates,
        }
    return None


async def _build_contextual_casual_followup_response(
    *,
    state: OrchestratorState,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    text: str,
    conversational_locale: str,
    conversational_locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    conversation_responder: ConversationResponder | None,
    route_logger: Any | None,
) -> dict[str, Any] | None:
    contextual_casual_followup = state_view.has_no_active_flow and is_contextual_casual_followup_turn(
        text,
        state_view.loaded_context_or_empty.get("history"),
    )
    if not contextual_casual_followup:
        return None

    responder_reply = await _build_bounded_conversational_reply(
        state_view=state_view,
        text=text,
        locale=conversational_locale,
        conversation_responder=conversation_responder,
    )
    if not responder_reply:
        return None

    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="conversation_responder",
        route_reason="contextual_casual_followup",
        policy_blocked=False,
        fallback_path="planner_non_task",
        route_logger=route_logger,
    )
    return {
        "final_response": responder_reply,
        **conversational_locale_updates,
        **context_read_updates,
    }


__all__ = [
    "_build_banking_ambiguity_response",
    "_build_contextual_casual_followup_response",
]
