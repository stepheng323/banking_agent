"""Planner no-task route breadcrumb logging."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import planner_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _log_unexpected_turn_route(
    *,
    state: OrchestratorState,
    planner_output: Any,
    selected_route: str,
    route_reason: str,
    policy_blocked: bool,
    fallback_path: str | None,
    route_logger: Any | None = None,
) -> None:
    active_logger = route_logger or logger
    state_view = planner_state_view(state)
    active_logger.info(
        "unexpected_turn_route_breadcrumb",
        user_turn_kind=str(getattr(planner_output, "primary_intent", "unknown") or "unknown"),
        active_session_present=state_view.has_session_stack,
        selected_route=selected_route,
        route_reason=route_reason,
        policy_blocked=policy_blocked,
        fallback_path=fallback_path,
    )


__all__ = ["_log_unexpected_turn_route"]
