"""Global flow session management for all transaction types."""

import json
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SESSION_TTL = 3600  # 1 hour


class FlowSessionManager:
    """Manages flow session data in Redis.

    Generic session manager for multi-step flows like:
    - Onboarding (BVN verification, account selection)
    - Account linking
    - Transaction confirmations
    """

    def __init__(
        self,
        redis: Any = None,
        key_prefix: str = "flow",
        ttl: int = DEFAULT_SESSION_TTL,
    ):
        self.redis: Any = redis or RedisClient.get_client()
        self.key_prefix = key_prefix
        self.ttl = ttl

    def _session_key(self, flow_token: str) -> str:
        return f"{self.key_prefix}:{flow_token}"

    async def get_session(self, flow_token: str) -> dict[str, Any]:
        """Get session data from Redis."""
        try:
            data = await self.redis.get(self._session_key(flow_token))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error("get_session_error", error=str(e))
        return {}

    async def update_session(self, flow_token: str, updates: dict[str, Any]) -> None:
        """Merge updates into existing session."""
        try:
            existing = await self.get_session(flow_token)
            existing.update(updates)
            await self.redis.set(self._session_key(flow_token), json.dumps(existing), ex=self.ttl)
        except Exception as e:
            logger.error("update_session_error", error=str(e))

    async def delete_session(self, flow_token: str) -> None:
        """Delete session from Redis."""
        try:
            await self.redis.delete(self._session_key(flow_token))
        except Exception as e:
            logger.error("delete_session_error", error=str(e))
