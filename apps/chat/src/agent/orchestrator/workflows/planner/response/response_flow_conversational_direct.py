from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_locale import _build_policy_aware_greeting
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_common import _localized_planner_response
from apps.chat.src.agent.orchestrator.workflows.planner.response.response_flow_logging import _log_unexpected_turn_route
from banking.presentation.i18n.message_keys import is_message_key
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _direct_planner_response(
    *,
    state: OrchestratorState,
    planner_output: Any,
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


def _response_key_render_response(
    *,
    state: OrchestratorState,
    planner_output: Any,
    response_key: str,
    conversational_locale: str,
    conversational_locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    route_logger: Any | None,
) -> dict[str, Any]:
    logger.info("planner_response_key_used", key=response_key, locale=conversational_locale)
    if response_key == "conversational.greeting":
        return {
            "final_response": _build_policy_aware_greeting(conversational_locale),
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
    message_key = response_key if is_message_key(response_key) else "response.fallback.generic"
    return {
        "final_response": render_message(message_key, conversational_locale),
        **conversational_locale_updates,
        **context_read_updates,
    }


__all__ = ["_direct_planner_response", "_response_key_render_response"]
