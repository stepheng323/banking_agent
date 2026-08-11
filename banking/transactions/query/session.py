"""Read-only helpers for typed query continuation snapshots."""

import time
from typing import Any

from shared.utils.logging import get_logger

logger = get_logger(__name__)

SESSION_TTL = 300


def _session_has_surface_view(session: dict[str, Any]) -> bool:
    """Return whether the snapshot carries typed surface state."""
    query_result = session.get("display_result")
    if hasattr(query_result, "surface_view"):
        return getattr(query_result, "surface_view", None) is not None
    if isinstance(query_result, dict):
        return bool(query_result.get("surface_view"))
    return False


def is_query_session_stale(
    session: dict[str, Any], *, now: float | None = None, ttl_seconds: int = SESSION_TTL
) -> bool:
    """Return True when a session snapshot is outside configured TTL."""
    raw_timestamp = session.get("timestamp")
    if raw_timestamp is None:
        return False

    try:
        saved_at = float(raw_timestamp)
    except (TypeError, ValueError):
        logger.warning("query_session_timestamp_invalid", raw_timestamp=raw_timestamp)
        return True

    current_time = time.time() if now is None else float(now)
    return current_time - saved_at > ttl_seconds
