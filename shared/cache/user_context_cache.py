"""User context cache backed by Redis."""

from __future__ import annotations

from typing import Any, Optional

import json
import redis.asyncio as redis

from shared.config import settings
from shared.cache.redis_client import RedisClient


class UserContextCacheService:
    """Caches per-user context in Redis for multi-turn conversations."""

    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Initialize user context cache service."""
        self._redis = redis_client or RedisClient.get_client()
        self._ttl_seconds = ttl_seconds or settings.user_ctx_ttl_seconds

    def _key(self, phone_number: str) -> str:
        return f"user:{phone_number}:ctx"

    async def get(self, phone_number: str) -> Optional[dict[str, Any]]:
        """Get user Context"""
        value = await self._redis.get(self._key(phone_number))
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None

    async def set(
        self,
        phone_number: str,
        context: dict[str, Any],
        ttl_seconds: Optional[int] = None,
    ) -> None:
        ttl = ttl_seconds or self._ttl_seconds
        await self._redis.set(self._key(phone_number), json.dumps(context), ex=ttl)

    async def invalidate(self, phone_number: str) -> None:
        await self._redis.delete(self._key(phone_number))
