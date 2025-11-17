"""Redis-based cache for Nigerian banks data."""

import json
from typing import List, Dict, Optional, Callable, Awaitable, Any
from datetime import datetime
import redis.asyncio as redis

from shared.cache.redis_client import RedisClient


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
                print(f"✅ Loaded {len(banks)} banks from Redis cache")
                return banks

            print("⚠️  Redis cache miss - banks not found")
            return None
        except Exception as e:
            print(f"⚠️  Redis get error: {e}")
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

            print(f"✅ Stored {len(banks)} banks in Redis (TTL: {ttl}s)")
            return True
        except Exception as e:
            print(f"⚠️  Redis set error: {e}")
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
            print(f"⚠️  Redis timestamp error: {e}")
            return None

    async def refresh_banks(self, fetch_func) -> List[Dict[str, str]]:
        """
        Force refresh banks from Flutterwave and update cache.

        Args:
            fetch_func: Async function to fetch banks from Flutterwave

        Returns:
            List of banks
        """
        try:
            print("🔄 Force refreshing banks from Flutterwave...")
            banks = await fetch_func()

            if banks:
                await self.set_banks(banks, ttl=86400)
                print(f"✅ Refreshed {len(banks)} banks")
                return banks

            print("⚠️  Failed to refresh banks from Flutterwave")
            return []
        except Exception as e:
            print(f"⚠️  Refresh error: {e}")
            return []

    async def clear_cache(self) -> bool:
        """
        Clear banks cache from Redis.

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            await self.redis.delete(self.CACHE_KEY)
            await self.redis.delete(self.TIMESTAMP_KEY)
            print("✅ Cleared banks cache")
            return True
        except Exception as e:
            print(f"⚠️  Clear cache error: {e}")
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
            print(
                f"⚠️  get_bank_code: No banks in cache for lookup: {bank_name}")
            return None

        normalized_name = bank_name.lower().strip()
        print(
            f"DEBUG get_bank_code: Looking up '{bank_name}' (normalized: '{normalized_name}') in {len(banks)} banks")

        # Common bank abbreviations mapping (prioritize these)
        # Maps abbreviations to possible full names or key terms to search for
        bank_abbreviations = {
            "uba": ["uba", "united bank for africa"],
            "gtb": ["gtbank", "guaranty trust bank"],
            "gtbank": ["gtbank", "guaranty trust bank"],
            "access": ["access bank"],
            "access bank": ["access bank"],
            "zenith": ["zenith bank"],
            "first bank": ["first bank", "firstbank"],
            "firstbank": ["first bank", "firstbank"],
            "opay": ["opay"],
            "palmpay": ["palmpay", "palm pay"],
            "kuda": ["kuda"],
        }

        # Check abbreviations first
        if normalized_name in bank_abbreviations:
            target_terms = bank_abbreviations[normalized_name]
            for bank in banks:
                bank_name_lower = bank.get("name", "").lower()
                # Check if any target term matches the bank name (exact or contains)
                for target_term in target_terms:
                    if target_term == bank_name_lower or target_term in bank_name_lower or bank_name_lower in target_term:
                        code = bank.get("code")
                        print(
                            f"✅ get_bank_code: Abbreviation match found - '{bank.get('name')}' -> {code}")
                        return code

        # Try exact match first
        for bank in banks:
            bank_name_field = bank.get("name", "").lower().strip()
            if bank_name_field == normalized_name:
                code = bank.get("code")
                print(
                    f"✅ get_bank_code: Exact match found - '{bank.get('name')}' -> {code}")
                return code

        # Try matching without common suffixes
        normalized_no_suffix = normalized_name.replace(
            " bank", "").replace(" plc", "").replace(" limited", "").strip()
        for bank in banks:
            bank_name_field = bank.get("name", "").lower().strip()
            bank_name_no_suffix = bank_name_field.replace(" bank", "").replace(
                " plc", "").replace(" limited", "").strip()
            if normalized_no_suffix == bank_name_no_suffix:
                code = bank.get("code")
                print(
                    f"✅ get_bank_code: Suffix-stripped match found - '{bank.get('name')}' -> {code}")
                return code

        # Try partial match (contains) - but only for words, not substrings
        # This prevents "uba" from matching "Bubayero"
        # For short abbreviations (3 chars or less), check if they appear as standalone words
        if len(normalized_name) <= 3:
            for bank in banks:
                bank_name_field = bank.get("name", "").lower().strip()
                bank_words = bank_name_field.split()
                # Check if normalized_name is a complete word in bank name
                if normalized_name in bank_words:
                    code = bank.get("code")
                    print(
                        f"✅ get_bank_code: Abbreviation word match found - '{bank.get('name')}' -> {code}")
                    return code

        # For longer names, check word matches
        normalized_words = normalized_name.split()
        for bank in banks:
            bank_name_field = bank.get("name", "").lower().strip()
            bank_words = bank_name_field.split()
            # Check if any word from normalized_name is a complete word in bank name
            # OR if normalized_name is a complete word in bank name
            if (any(word in bank_words for word in normalized_words if len(word) >= 3) or
                    normalized_name in bank_words):
                code = bank.get("code")
                print(
                    f"✅ get_bank_code: Word match found - '{bank.get('name')}' -> {code}")
                return code

        print(f"⚠️  get_bank_code: No match found for '{bank_name}'")
        return None

    async def ensure_banks_cached(
        self,
        fetch_func: Callable[[], Awaitable[Dict[str, Any]]]
    ) -> bool:
        """
        Ensure banks are cached. Fetch from provider if cache is empty.

        Args:
            fetch_func: Async function that returns bank data from payment provider.
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
            print("🔄 Bank cache miss - fetching from payment provider...")
            result = await fetch_func()

            if result.get("success") and result.get("banks"):
                banks_list = result["banks"]
                await self.set_banks(banks_list, ttl=86400)
                print(f"✅ Fetched and cached {len(banks_list)} banks")
                return True
            else:
                error = result.get("error", "Unknown error")
                print(f"⚠️  Failed to fetch banks: {error}")
                return False
        except Exception as e:
            print(f"⚠️  Error fetching banks: {e}")
            return False
