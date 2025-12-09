"""Redis-based cache for Nigerian banks data."""

import json
from typing import List, Dict, Optional, Callable, Awaitable, Any
from datetime import datetime
import redis.asyncio as redis

from shared.cache.redis_client import RedisClient
from shared.utils.bank_aliases import normalize_bank_name, get_bank_search_terms
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BankCacheService:
    """Manages Redis cache for Nigerian banks from Flutterwave API."""

    CACHE_KEY = "nigerian_banks"
    TIMESTAMP_KEY = "nigerian_banks:timestamp"

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        """Initialize bank cache service."""
        self.redis = redis_client or RedisClient.get_client()

    async def get_banks(self) -> Optional[List[Dict[str, str]]]:
        """Get banks from Redis cache."""
        try:
            cached_data = await self.redis.get(self.CACHE_KEY)
            if cached_data:
                banks = json.loads(cached_data)
                logger.info("banks_cache_hit", count=len(banks))
                return banks

            logger.debug("banks_cache_miss")
            return None
        except Exception as e:
            logger.error("banks_cache_get_error", error=str(e), exc_info=True)
            return None

    async def set_banks(self, banks: List[Dict[str, str]], ttl: int = 86400) -> bool:
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

            logger.info("banks_cached", count=len(banks), ttl_seconds=ttl)
            return True
        except Exception as e:
            logger.error("banks_cache_set_error", error=str(e), exc_info=True)
            return False

    async def get_last_updated(self) -> Optional[str]:
        """
        Get timestamp of when cache was last updated.

        Returns:
            ISO format timestamp string or None if not available
        """
        try:
            timestamp = await self.redis.get(self.TIMESTAMP_KEY)
            if timestamp:
                # Redis is configured with decode_responses=True, so no need to decode
                return timestamp if isinstance(timestamp, str) else timestamp.decode('utf-8')
            return None
        except Exception as e:
            logger.error("banks_timestamp_get_error", error=str(e), exc_info=True)
            return None

    async def refresh_banks(self, fetch_banks_func: Callable[[], Awaitable[Dict[str, Any]]]) -> Optional[List[Dict[str, str]]]:
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

    async def get_bank_code(self, bank_name: str) -> Optional[str]:
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

        normalized_name = bank_name.lower().strip()
        logger.debug("bank_code_lookup_started", bank_name=bank_name, normalized=normalized_name, total_banks=len(banks))

        # Use centralized bank aliases for search terms
        search_terms = get_bank_search_terms(bank_name)
        normalized = normalize_bank_name(bank_name)

        # Check using search terms from centralized aliases
        for bank in banks:
            bank_name_lower = bank.get("name", "").lower()
            for term in search_terms:
                if term in bank_name_lower or bank_name_lower in term:
                    code = bank.get("code")
                    logger.info("bank_code_found", match_type="alias", bank_name=bank.get('name'), code=code, search_term=bank_name)
                    return code

        # Try exact match
        for bank in banks:
            bank_name_field = bank.get("name", "").lower().strip()
            if bank_name_field == normalized_name or bank_name_field == normalized:
                code = bank.get("code")
                logger.info("bank_code_found", match_type="exact", bank_name=bank.get('name'), code=code, search_term=bank_name)
                return code

        # Try matching without common suffixes
        normalized_no_suffix = normalized_name.replace(" bank", "").replace(" plc", "").replace(" limited", "").strip()
        for bank in banks:
            bank_name_field = bank.get("name", "").lower().strip()
            bank_name_no_suffix = bank_name_field.replace(" bank", "").replace(" plc", "").replace(" limited", "").strip()
            if normalized_no_suffix == bank_name_no_suffix:
                code = bank.get("code")
                logger.info("bank_code_found", match_type="suffix_stripped", bank_name=bank.get('name'), code=code, search_term=bank_name)
                return code

        # For short abbreviations (3 chars or less), check if they appear as standalone words
        if len(normalized_name) <= 3:
            for bank in banks:
                bank_name_field = bank.get("name", "").lower().strip()
                bank_words = bank_name_field.split()
                if normalized_name in bank_words:
                    code = bank.get("code")
                    logger.info("bank_code_found", match_type="word", bank_name=bank.get('name'), code=code, search_term=bank_name)
                    return code

        # For longer names, check word matches
        normalized_words = normalized_name.split()
        for bank in banks:
            bank_name_field = bank.get("name", "").lower().strip()
            bank_words = bank_name_field.split()
            if any(word in bank_words for word in normalized_words if len(word) >= 3) or normalized_name in bank_words:
                code = bank.get("code")
                logger.info("bank_code_found", match_type="word", bank_name=bank.get('name'), code=code, search_term=bank_name)
                return code

        logger.warning("bank_code_not_found", bank_name=bank_name)
        return None

    async def ensure_banks_cached(
        self,
        fetch_banks_func: Callable[[], Awaitable[Dict[str, Any]]]
    ) -> bool:
        """
        Ensure banks are cached. Fetch from provider if cache is empty.

        Args:
            fetch_banks_func: Async function that returns bank data from payment provider.
                       Should return dict with 'success' bool and 'banks' list.

        Returns:
            True if banks are available (cached or fetched), False otherwise
        """
        # Check if banks exist in cache
        banks = await self.get_banks()
        if banks:
            return True

        # Cache miss - fetch from provider
        try:
            logger.info("banks_cache_miss_fetching")
            result = await fetch_banks_func()

            if result.get("success") and result.get("banks"):
                banks_list = result["banks"]
                await self.set_banks(banks_list, ttl=86400)
                logger.info("banks_fetched_and_cached", count=len(banks_list))
                return True
            else:
                error = result.get("error", "Unknown error")
                logger.error("banks_fetch_failed", error=error)
                return False
        except Exception as e:
            logger.error("banks_ensure_error", error=str(e), exc_info=True)
            return False
