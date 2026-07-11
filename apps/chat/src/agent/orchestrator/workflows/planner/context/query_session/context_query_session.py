"""Query surface snapshot helpers for planner context construction."""
from typing import Any, Protocol

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.context.query_surface import (
    build_query_session_snapshot_from_surface,
    get_active_query_surface,
    summarize_query_surface_for_planner,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_core import (
    _build_query_session_context,
)
from banking.transactions.query.session import _session_has_surface_view, is_query_session_stale
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_STASHED_COMPAT_KEYS = {
    "session_active",
    "query_contract",
    "query_result",
    "query_frames",
    "pending_clarification",
    "current_page",
    "page_size",
    "show_expanded",
    "timestamp",
    "account_id",
    "account_ids",
    "cache_fingerprint",
    "cache_scope_fingerprint",
    "cache_window_start",
    "cache_window_end",
}


class QuerySessionStateView(Protocol):
    @property
    def phone_number(self) -> str: ...

    @property
    def pending_query_clarification(self) -> dict[str, Any] | None: ...

    @property
    def context_frames(self) -> list[ContextFrame]: ...


async def _load_query_session_snapshot(
    state_view: QuerySessionStateView,
    *,
    snapshot_logger: Any | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Project the canonical query context frame, with compact stashed compatibility fallback."""
    query_session_snapshot: dict[str, Any] | None = None
    query_session_source: str | None = None

    active_query_surface = get_active_query_surface(state_view)
    if active_query_surface is not None:
        query_session_snapshot = build_query_session_snapshot_from_surface(active_query_surface)
        if query_session_snapshot is not None:
            query_session_snapshot["active_query_surface"] = active_query_surface
            query_session_source = "context_frame"

    if query_session_snapshot is None and isinstance(state_view.pending_query_clarification, dict):
        query_session_snapshot = _pending_clarification_snapshot(state_view.pending_query_clarification)
        query_session_source = "pending_clarification"
        if is_query_session_stale(query_session_snapshot):
            query_session_snapshot["session_active"] = False

    if query_session_snapshot is None:
        recent = getattr(state_view, "recent_query_context", None)
        if isinstance(recent, dict) and int(recent.get("remaining_turns", -1) or 0) >= 0:
            candidate = recent.get("session")
            if isinstance(candidate, dict):
                query_session_snapshot = dict(candidate)
                query_session_snapshot["session_active"] = False
                query_session_snapshot["_query_session_source"] = "recent_closed_context"
                query_session_source = "recent_closed_context"

    snapshot = query_session_snapshot if isinstance(query_session_snapshot, dict) else {}
    active_logger = snapshot_logger or logger
    active_logger.info(
        "planner_query_session_snapshot",
        query_session_source=query_session_source or "none",
        session_active=bool(snapshot.get("session_active")),
        has_query_contract=bool(snapshot.get("query_contract")),
        has_query_result=bool(snapshot.get("query_result")),
        has_surface=_session_has_surface_view(snapshot),
        has_query_frames=bool(snapshot.get("query_frames")),
    )

    return query_session_snapshot, query_session_source


def _pending_clarification_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build an active query snapshot from first-class pending clarification state."""
    compact = {key: value for key, value in snapshot.items() if key in _STASHED_COMPAT_KEYS}
    compact["session_active"] = True
    return compact


def _query_session_summary_text(query_session_snapshot: dict[str, Any] | None) -> tuple[str | None, bool]:
    if not isinstance(query_session_snapshot, dict):
        return None, False
    session_active = bool(query_session_snapshot.get("session_active"))
    if query_session_snapshot.get("_query_session_source") == "context_frame":
        raw_surface = query_session_snapshot.get("active_query_surface")
        if isinstance(raw_surface, ContextFrame):
            return summarize_query_surface_for_planner(raw_surface), session_active
    summary_text = None
    query_result = query_session_snapshot.get("query_result")
    if isinstance(query_result, dict):
        summary_text = query_result.get("summary_text")
    pending_clarification = query_session_snapshot.get("pending_clarification")
    return (
        _build_query_session_context(
            summary_text if isinstance(summary_text, str) else None,
            pending_clarification if isinstance(pending_clarification, dict) else None,
        ),
        session_active,
    )


__all__ = ["_load_query_session_snapshot", "_query_session_summary_text"]
