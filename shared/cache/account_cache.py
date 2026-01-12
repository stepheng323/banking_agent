"""Redis-based cache for resolved bank accounts."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountCacheService:
    """
    Manages Redis cache for resolved bank accounts.

    Uses a global key strategy so all users benefit from cached account details.
    Key format: account:{bank_code}:{account_number}
    """

    TTL_SECONDS = 604800  # 7 days

    def __init__(self, redis_client: redis.Redis | None = None):
        """Initialize account cache service."""
        self.redis = redis_client or RedisClient.get_client()

    def _get_key(self, account_number: str, bank_code: str) -> str:
        """Generate cache key."""
        return f"account:{bank_code}:{account_number}"

    async def get_account(self, account_number: str, bank_code: str) -> dict[str, Any] | None:
        """Get account details from cache."""
        try:
            key = self._get_key(account_number, bank_code)
            cached_data = await self.redis.get(key)

            if cached_data:
                account = json.loads(cached_data)
                logger.info(
                    "account_cache_hit",
                    account_number=account_number,
                    bank_code=bank_code,
                    account_name=account.get("account_name")
                )
                return account

            logger.debug("account_cache_miss", account_number=account_number, bank_code=bank_code)
            return None
        except Exception as e:
            logger.error("account_cache_get_error", error=str(e), exc_info=True)
            return None

    async def set_account(
        self, 
        account_number: str, 
        bank_code: str, 
        data: dict[str, Any]
    ) -> bool:
        """Cache account details."""
        try:
            if not data.get("success"):
                return False

            key = self._get_key(account_number, bank_code)
            serialized = json.dumps(data)
            await self.redis.setex(key, self.TTL_SECONDS, serialized)

            logger.info("account_cached", account_number=account_number, bank_code=bank_code)
            return True
        except Exception as e:
            logger.error("account_cache_set_error", error=str(e), exc_info=True)
            return False

    async def get_or_fetch(
        self,
        account_number: str,
        bank_code: str,
        fetch_func: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """
        Get account from cache or fetch from provider if missing.
        """
        # 1. Try Cache
        cached = await self.get_account(account_number, bank_code)
        if cached:
            return cached

        # 2. Fetch from Provider
        try:
            logger.info("account_cache_miss_fetching", account_number=account_number)
            result = await fetch_func()

            # 3. Cache if successful
            if result.get("success"):
                await self.set_account(account_number, bank_code, result)

            return result
        except Exception as e:
            logger.error("account_fetch_error", error=str(e), exc_info=True)
            # Return a minimal error structure rather than crashing
            return {
                "success": False,
                "error": str(e),
                "account_number": account_number,
                "bank_code": bank_code
            }
