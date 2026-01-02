"""Session management for query flow."""

import json
from typing import Any

import redis.asyncio as redis

from shared.utils.logging import get_logger

logger = get_logger(__name__)

SESSION_TTL = 300


class QuerySessionManager:
    """Manages query session state in Redis."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def load(self, key: str) -> dict[str, Any] | None:
        """Load session state from Redis and restore Pydantic models."""
        from apps.core.src.agent.sub_agents.query.models import NormalizedQuery, QueryResult

        try:
            data = await self.redis.get(key)
            if not data:
                return None

            session = json.loads(data)

            if session.get("query") and isinstance(session["query"], dict):
                try:
                    session["query"] = NormalizedQuery.model_validate(session["query"])
                except Exception as e:
                    logger.warning("query_restore_error", error=str(e))
                    session["query"] = None

            if session.get("query_result") and isinstance(session["query_result"], dict):
                try:
                    session["query_result"] = QueryResult.model_validate(session["query_result"])
                except Exception as e:
                    logger.warning("query_result_restore_error", error=str(e))
                    session["query_result"] = None

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
                "language",
                "session_active",
                "query",
                "query_result",
                "pending_support_item",
                "show_expanded",
                "clarification_attempts",
                "last_successful_query",
                "confidence_level",
                "recipient_name",
                "filters",
            )
            for k, v in state.items():
                if k not in allowed_keys:
                    continue
                if k in ("query", "query_result") and v and hasattr(v, "model_dump"):
                    save_state[k] = v.model_dump()
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
