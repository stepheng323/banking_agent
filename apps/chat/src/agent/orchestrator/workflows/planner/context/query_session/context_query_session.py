"""Query session snapshot helpers for planner context construction."""

import json
from typing import Any, Protocol

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_core import (
    _build_query_session_context,
)
from banking.transactions.query.session import _session_has_surface_view, is_query_session_stale
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QuerySessionStateView(Protocol):
    @property
    def phone_number(self) -> str: ...

    @property
    def stashed_query_session(self) -> dict[str, Any] | None: ...


async def _load_query_session_snapshot(
    state_view: QuerySessionStateView,
    redis_client: redis.Redis | None,
    *,
    snapshot_logger: Any | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    query_session_snapshot: dict[str, Any] | None = None
    query_session_source: str | None = None

    if redis_client:
        try:
            query_session_key = f"query:session:{state_view.phone_number}"
            query_session_data = await redis_client.get(query_session_key)
            if query_session_data:
                if isinstance(query_session_data, bytes):
                    query_session_data = query_session_data.decode("utf-8")
                parsed = json.loads(query_session_data)
                if isinstance(parsed, dict):
                    query_session_snapshot = parsed
                    query_session_source = "redis"
                    if is_query_session_stale(query_session_snapshot):
                        query_session_snapshot["session_active"] = False
        except Exception:
            query_session_snapshot = None
            query_session_source = None

    if query_session_snapshot is None and isinstance(state_view.stashed_query_session, dict):
        query_session_snapshot = dict(state_view.stashed_query_session)
        query_session_source = "stashed"
        if is_query_session_stale(query_session_snapshot):
            query_session_snapshot["session_active"] = False

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


def _query_session_summary_text(query_session_snapshot: dict[str, Any] | None) -> tuple[str | None, bool]:
    if not isinstance(query_session_snapshot, dict):
        return None, False
    session_active = bool(query_session_snapshot.get("session_active"))
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
