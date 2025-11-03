"""Redis-based cache for Nigerian banks data."""

import json
from typing import List, Dict, Optional
from datetime import datetime
import redis.asyncio as redis


class BankCacheService:
    """Manages Redis cache for Nigerian banks from Flutterwave API."""

    CACHE_KEY = "nigerian_banks"
    TIMESTAMP_KEY = "nigerian_banks:timestamp"

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize bank cache service.

        Args:
            redis_client: Async Redis client instance
        """
        self.redis = redis_client

    async def get_banks(self) -> Optional[List[Dict[str, str]]]:
        """
        Get banks from Redis cache.

        Returns:
            List of banks if cache hit, None if cache miss or expired
        """
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
            # Serialize and store banks
            serialized = json.dumps(banks)
            await self.redis.setex(self.CACHE_KEY, ttl, serialized)

            # Store timestamp
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
                return timestamp.decode('utf-8')
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
