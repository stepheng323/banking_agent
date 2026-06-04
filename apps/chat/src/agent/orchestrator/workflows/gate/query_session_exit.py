"""State updates for leaving query sessions from gate-owned routes."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.state_view import gate_state_view


def _build_query_session_exit_updates(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    state_view = gate_state_view(state)
    has_query_snapshot = isinstance(query_session_snapshot, dict) and bool(query_session_snapshot.get("session_active"))
    has_query_session_stack = state_view.has_session_for_domain("query")
    if not has_query_snapshot and not has_query_session_stack and state_view.active_domain != "query":
        return {}
    remaining_sessions = state_view.session_stack_without_domain("query")
    return {
        "stashed_query_session": None,
        "session_stack": remaining_sessions,
        "active_domain": None if state_view.active_domain == "query" else state_view.active_domain,
    }
