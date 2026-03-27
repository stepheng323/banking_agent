"""Session management for query flow."""

import json
import time
from typing import Any

import redis.asyncio as redis

from apps.core.src.agent.graphs.query.models import PendingClarificationState, QueryExecutionContract, QueryResult
from apps.core.src.agent.graphs.query.services.grounding import restore_query_frames
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SESSION_TTL = 300


def is_query_session_stale(session: dict[str, Any], *, now: float | None = None, ttl_seconds: int = SESSION_TTL) -> bool:
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
    """Manages query session state in Redis."""

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

            if session.get("query_result") and isinstance(session["query_result"], dict):
                try:
                    session["query_result"] = QueryResult.model_validate(session["query_result"])
                except Exception as e:
                    logger.warning("query_result_restore_error", error=str(e))
                    session["query_result"] = None

            if session.get("pending_clarification") and isinstance(session["pending_clarification"], dict):
                try:
                    session["pending_clarification"] = PendingClarificationState.model_validate(
                        session["pending_clarification"]
                    )
                except Exception as e:
                    logger.warning("pending_clarification_restore_error", error=str(e))
                    session["pending_clarification"] = None

            if session.get("surface") and isinstance(session["surface"], dict):
                try:
                    from apps.core.src.agent.graphs.query.models import ResultSurface

                    session["surface"] = ResultSurface.model_validate(session["surface"])
                except Exception as e:
                    logger.warning("surface_restore_error", error=str(e))
                    session["surface"] = None

            if session.get("query_frames"):
                session["query_frames"] = restore_query_frames(session["query_frames"])

            if is_query_session_stale(session):
                logger.info(
                    "query_session_stale_disarmed",
                    key=key,
                    session_active=bool(session.get("session_active")),
                )
                session["session_active"] = False
                session["query_contract"] = None
                session["query_result"] = None
                session["pending_clarification"] = None
                session["query_frames"] = []
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
        """Save session state to Redis."""
        try:
            save_state = {}
            allowed_keys = (
                "phone_number",
                "account_id",
                "account_ids",
                "accounts",
                "current_account_index",
                "account_info",
                "current_page",
                "page_size",
                "total_results",
                "has_more",
                "cached_transactions",
                "cache_fetched_at",
                "cache_fingerprint",
                "cache_scope_fingerprint",
                "cache_window_start",
                "cache_window_end",
                "language",
                "session_active",
                "query_contract",
                "query_result",
                "show_expanded",
                "clarification_attempts",
                "recipient_name",
                "filters",
                "surface",
                "pending_clarification",
                "query_frames",
                "timestamp",
            )
            for k, v in state.items():
                if k not in allowed_keys:
                    continue
                if k in ("query_contract", "query_result", "surface", "pending_clarification") and v and hasattr(
                    v, "model_dump"
                ):
                    save_state[k] = v.model_dump()
                elif k == "query_frames" and isinstance(v, list):
                    save_state[k] = [frame.model_dump() if hasattr(frame, "model_dump") else frame for frame in v]
                elif k == "cached_transactions" and v:
                    save_state[k] = [t.model_dump() if hasattr(t, "model_dump") else t for t in v]
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
