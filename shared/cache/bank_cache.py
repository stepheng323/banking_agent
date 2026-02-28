"""Redis-based cache for Nigerian banks data."""

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from shared.cache.redis_client import RedisClient
from shared.utils.bank_aliases import get_bank_search_terms
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BankCacheService:
    """Manages Redis cache for Nigerian banks from Flutterwave API."""

    CACHE_KEY = "nigerian_banks"
    TIMESTAMP_KEY = "nigerian_banks:timestamp"

    def __init__(self, redis_client: redis.Redis | None = None):
        """Initialize bank cache service."""
        self.redis = redis_client or RedisClient.get_client()
        self._search_index: dict[str, str] = {}

    def _build_search_index(self, banks: list[dict[str, str]]) -> None:
        """Build in-memory search index for faster lookups."""
        self._search_index.clear()

        suffixes = [" bank", " plc", " limited", " microfinance bank"]

        for bank in banks:
            code = bank.get("code") or bank.get("bank_code")
            name = bank.get("name", "")
            if not code or not name:
                continue

            # 1. Exact match (lowercase)
            self._search_index[name.lower().strip()] = code

            # 2. Aliases/Search terms
            terms = get_bank_search_terms(name)
            for term in terms:
                self._search_index[term] = code

            # 3. Suffix stripped
            name_lower = name.lower().strip()
            for suffix in suffixes:
                name_lower = name_lower.replace(suffix, "")
            name_clean = name_lower.strip()
            if name_clean:
                self._search_index[name_clean] = code

    async def get_banks(self) -> list[dict[str, str]] | None:
        """Get banks from Redis cache."""
        try:
            cached_data = await self.redis.get(self.CACHE_KEY)
            if cached_data:
                banks = json.loads(cached_data)
                # Rebuild index if empty (e.g. after service restart)
                if not self._search_index and banks:
                    self._build_search_index(banks)
                logger.info("banks_cache_hit", count=len(banks))
                return banks

            logger.debug("banks_cache_miss")
            return None
        except Exception as e:
            logger.error("banks_cache_get_error", error=str(e), exc_info=True)
            return None

    async def set_banks(self, banks: list[dict[str, str]], ttl: int = 86400) -> bool:
        """
        Store banks in Redis cache with TTL.

        Args:
            banks: List of bank dictionaries with id, code, name
            ttl: Time-to-live in seconds (default: 86400 = 24 hours)

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            serialized = json.dumps(banks)
            await self.redis.setex(self.CACHE_KEY, ttl, serialized)

            timestamp = datetime.utcnow().isoformat()
            await self.redis.setex(self.TIMESTAMP_KEY, ttl, timestamp)

            # Update in-memory index
            self._build_search_index(banks)

            logger.info("banks_cached", count=len(banks), ttl_seconds=ttl)
            return True
        except Exception as e:
            logger.error("banks_cache_set_error", error=str(e), exc_info=True)
            return False

    async def get_last_updated(self) -> str | None:
        """
        Get timestamp of when cache was last updated.

        Returns:
            ISO format timestamp string or None if not available
        """
        try:
            timestamp = await self.redis.get(self.TIMESTAMP_KEY)
            if timestamp:
                # Redis is configured with decode_responses=True, so no need to decode
                return timestamp if isinstance(timestamp, str) else timestamp.decode("utf-8")
            return None
        except Exception as e:
            logger.error("banks_timestamp_get_error", error=str(e), exc_info=True)
            return None

    async def refresh_banks(
        self, fetch_banks_func: Callable[[], Awaitable[dict[str, Any]]]
    ) -> list[dict[str, str]] | None:
        """
        Force refresh banks from Flutterwave and update cache.

        Args:
            fetch_banks_func: Async function that returns bank data from payment provider.
                       Should return dict with 'success' bool and 'banks' list.

        Returns:
            List of banks
        """
        try:
            logger.info("banks_refresh_started")
            result = await fetch_banks_func()

            if result.get("success") and result.get("banks"):
                banks = result["banks"]
                await self.set_banks(banks)
                logger.info("banks_refreshed", count=len(banks))
                return banks

            logger.warning("banks_refresh_failed", result=result)
            return None
        except Exception as e:
            logger.error("banks_refresh_error", error=str(e), exc_info=True)
            return None

    async def clear_cache(self) -> bool:
        """
        Clear banks cache from Redis.

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            await self.redis.delete(self.CACHE_KEY)
            await self.redis.delete(self.TIMESTAMP_KEY)
            logger.info("banks_cache_cleared")
            return True
        except Exception as e:
            logger.error("banks_cache_clear_error", error=str(e), exc_info=True)
            return False

    async def get_bank_code(self, bank_name: str) -> str | None:
        """
        Lookup bank code from bank name using cached bank list.

        Args:
            bank_name: Bank name to lookup (e.g., "Access bank", "GTB")

        Returns:
            Bank code if found, None otherwise
        """
        banks = await self.get_banks()
        if not banks:
            logger.warning("bank_code_lookup_no_cache", bank_name=bank_name)
            return None

        # 0. Check in-memory index first (O(1))
        normalized = bank_name.lower().strip()
        if normalized in self._search_index:
            code = self._search_index[normalized]
            logger.info("bank_code_index_hit", bank_name=bank_name, code=code)
            return code

        from shared.utils.bank_aliases import find_matching_bank_name

        # Create list of bank names for matching
        bank_names = [b.get("name", "") for b in banks]

        # Use centralized matching logic
        matched_name = find_matching_bank_name(bank_name, bank_names)

        if matched_name:
            # Find the bank object for the matched name
            for bank in banks:
                if bank.get("name") == matched_name:
                    code: str | None = bank.get("code") or bank.get("bank_code")
                    logger.info(
                        "bank_code_found",
                        bank_name=matched_name,
                        code=code,
                        search_term=bank_name,
                    )
                    return code

        logger.warning("bank_code_not_found", bank_name=bank_name)
        return None

    async def ensure_banks_cached(
        self, fetch_banks_func: Callable[[], Awaitable[dict[str, Any]]]
    ) -> list[dict[str, str]] | None:
        """
        Ensure banks are cached. Fetch from provider if cache is empty.

        Args:
            fetch_banks_func: Async function that returns bank data from payment provider.
                       Should return dict with 'success' bool and 'banks' list.

        Returns:
            List of banks if available (cached or fetched), None otherwise
        """
        # Check if banks exist in cache
        banks = await self.get_banks()
        if banks:
            return banks

        # Cache miss - fetch from provider
        try:
            logger.info("banks_cache_miss_fetching")
            result = await fetch_banks_func()

            if result.get("success") and result.get("banks"):
                banks_list = result["banks"]
                await self.set_banks(banks_list, ttl=86400)
                logger.info("banks_fetched_and_cached", count=len(banks_list))
                return banks_list
            else:
                error = result.get("error", "Unknown error")
                logger.error("banks_fetch_failed", error=error)
                return None
        except Exception as e:
            logger.error("banks_ensure_error", error=str(e), exc_info=True)
            return None
