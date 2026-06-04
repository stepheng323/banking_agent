"""Response assembly helpers for the planner runner."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.outcomes import (
    context_read_shortcut,
    failure_response,
    non_task_response,
    quoted_replay_dispatch,
    unavailable_response,
)
from shared.types.planner import PlannerOutput


def _planner_unavailable_response(current_locale: str) -> dict[str, Any]:
    return unavailable_response(current_locale)


def _quoted_replay_route_response(quoted_replay_updates: dict[str, Any]) -> dict[str, Any]:
    return quoted_replay_dispatch(quoted_replay_updates)


def _context_read_shortcut_response(shortcut_updates: dict[str, Any]) -> dict[str, Any]:
    return context_read_shortcut(shortcut_updates)


def _planner_failed_response() -> dict[str, Any]:
    return failure_response()


def _non_task_route_response(
    *,
    handled_response: dict[str, Any],
    planner_output: PlannerOutput,
) -> dict[str, Any]:
    return non_task_response(handled_response=handled_response, planner_output=planner_output)


__all__ = [
    "_context_read_shortcut_response",
    "_non_task_route_response",
    "_planner_failed_response",
    "_planner_unavailable_response",
    "_quoted_replay_route_response",
]
