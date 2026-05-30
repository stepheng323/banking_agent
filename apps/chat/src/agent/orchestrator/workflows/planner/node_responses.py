"""Response assembly helpers for the planner runner."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.node_updates import _planner_route_updates
from banking.presentation.i18n.bridge import render_safe_capability_fallback


def _planner_unavailable_response(current_locale: str) -> dict[str, Any]:
    return {
        "final_response": render_safe_capability_fallback(current_locale),
        **_planner_route_updates(decision="planner_unavailable"),
    }


def _quoted_replay_route_response(quoted_replay_updates: dict[str, Any]) -> dict[str, Any]:
    return {
        **quoted_replay_updates,
        **_planner_route_updates(decision="quoted_replay"),
    }


def _context_read_shortcut_response(shortcut_updates: dict[str, Any]) -> dict[str, Any]:
    return {
        **shortcut_updates,
        "semantic_path_shape": shortcut_updates.get("semantic_path_shape") or "planner",
        **_planner_route_updates(decision="planner_context_read"),
    }


def _planner_failed_response() -> dict[str, Any]:
    return _planner_route_updates(decision="planner_failed")


def _non_task_route_response(
    *,
    handled_response: dict[str, Any],
    planner_output: Any,
) -> dict[str, Any]:
    return {
        **handled_response,
        "semantic_path_shape": handled_response.get("semantic_path_shape") or "planner",
        **_planner_route_updates(
            decision=str(getattr(planner_output, "primary_intent", "") or "planner_non_task_response"),
            planner_output=planner_output,
        ),
    }


__all__ = [
    "_context_read_shortcut_response",
    "_non_task_route_response",
    "_planner_failed_response",
    "_planner_unavailable_response",
    "_quoted_replay_route_response",
]
