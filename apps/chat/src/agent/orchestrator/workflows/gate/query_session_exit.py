"""State updates for leaving query sessions from gate-owned routes."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _build_query_session_exit_updates(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    has_query_snapshot = isinstance(query_session_snapshot, dict) and bool(query_session_snapshot.get("session_active"))
    has_query_session_stack = any(session.domain == "query" for session in state.session_stack)
    if not has_query_snapshot and not has_query_session_stack and state.active_domain != "query":
        return {}
    remaining_sessions = [session for session in state.session_stack if session.domain != "query"]
    return {
        "stashed_query_session": None,
        "session_stack": remaining_sessions,
        "active_domain": None if state.active_domain == "query" else state.active_domain,
    }
