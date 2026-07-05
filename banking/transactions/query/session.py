"""Session management for query flow."""

import json
import time
from typing import Any

import redis.asyncio as redis

from banking.transactions.query.models.domain import QueryExecutionContract
from banking.transactions.query.models.extraction import PendingClarificationState
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SESSION_TTL = 300


def _session_has_surface_view(session: dict[str, Any]) -> bool:
    """Return whether the snapshot carries typed surface state."""
    query_result = session.get("query_result")
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


class QuerySessionManager:
    """Manages compact legacy query compatibility state in Redis."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def load(self, key: str) -> dict[str, Any] | None:
        """Load session state from Redis and restore Pydantic models."""

        try:
            data = await self.redis.get(key)
            if not data:
                return None

            loaded = json.loads(data)
            if not isinstance(loaded, dict):
                logger.warning("invalid_session_payload_type", payload_type=type(loaded).__name__)
                return None
            session: dict[str, Any] = loaded

            if session.get("query_contract") and isinstance(session["query_contract"], dict):
                try:
                    session["query_contract"] = QueryExecutionContract.model_validate(session["query_contract"])
                except Exception as e:
                    logger.warning("query_contract_restore_error", error=str(e))
                    session["query_contract"] = None

            # Successful query meaning now lives in orchestrator context frames.
            # Do not restore legacy result/frame snapshots from Redis.
            session.pop("query_result", None)
            session.pop("query_frames", None)
            session.pop("selected_item_index", None)
            session.pop("selected_payload", None)
            session.pop("cached_transactions", None)
            session.pop("cache_fetched_at", None)

            if session.get("pending_clarification") and isinstance(session["pending_clarification"], dict):
                try:
                    session["pending_clarification"] = PendingClarificationState.model_validate(
                        session["pending_clarification"]
                    )
                except Exception as e:
                    logger.warning("pending_clarification_restore_error", error=str(e))
                    session["pending_clarification"] = None

            if is_query_session_stale(session):
                logger.info(
                    "query_session_stale_disarmed",
                    key=key,
                    session_active=bool(session.get("session_active")),
                )
                session["session_active"] = False
                session["query_contract"] = None
                session["pending_clarification"] = None
                session["current_page"] = 0
                session["show_expanded"] = False
            else:
                try:
                    await self.redis.expire(key, SESSION_TTL)
                except Exception as exc:
                    logger.warning("refresh_session_ttl_failed", error=str(exc))

            return session
        except Exception as e:
            logger.error("load_session_error", error=str(e))
        return None

    async def save(self, key: str, state: dict[str, Any]) -> None:
        """Save compact compatibility state to Redis."""
        try:
            save_state = {}
            allowed_keys = (
                "phone_number",
                "account_id",
                "account_ids",
                "current_page",
                "page_size",
                "cache_fingerprint",
                "cache_scope_fingerprint",
                "cache_window_start",
                "cache_window_end",
                "language",
                "session_active",
                "query_contract",
                "show_expanded",
                "clarification_attempts",
                "pending_clarification",
                "timestamp",
            )
            for k, v in state.items():
                if k not in allowed_keys:
                    continue
                if k in ("query_contract", "pending_clarification") and v and hasattr(v, "model_dump"):
                    save_state[k] = v.model_dump()
                else:
                    save_state[k] = v
            await self.redis.set(key, json.dumps(save_state, default=str), ex=SESSION_TTL)
        except Exception as e:
            logger.error("save_session_error", error=str(e))

    async def clear(self, key: str) -> None:
        """Clear session state from Redis."""
        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.error("clear_session_error", error=str(e))
