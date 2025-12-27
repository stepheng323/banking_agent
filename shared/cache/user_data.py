"""User data cache service using Redis."""

import json
from datetime import timedelta
from typing import Any

from shared.cache.redis_client import Redis, RedisClient


class UserDataCache:
    """
    Cache service for frequently accessed user data.

    Caches:
    - User profiles
    - User accounts
    - Beneficiaries

    Uses cache-aside pattern with TTL.
    """

    # Cache TTLs
    PROFILE_TTL = int(timedelta(minutes=10).total_seconds())  # 10 minutes
    ACCOUNTS_TTL = int(timedelta(minutes=5).total_seconds())  # 5 minutes
    BENEFICIARIES_TTL = int(timedelta(minutes=5).total_seconds())  # 5 minutes

    def __init__(self, redis_client: Redis | None = None):
        self.redis = redis_client or RedisClient.get_client()

    # ============ User Profile Cache ============

    async def get_user_profile(self, phone_number: str) -> dict[str, Any] | None:
        """Get cached user profile."""
        key = f"cache:user:profile:{phone_number}"
        data = await self.redis.get(key)

        if data:
            return json.loads(data)
        return None

    async def set_user_profile(self, phone_number: str, profile: dict[str, Any]) -> None:
        """Cache user profile."""
        key = f"cache:user:profile:{phone_number}"
        await self.redis.set(key, json.dumps(profile), ex=self.PROFILE_TTL)

    async def invalidate_user_profile(self, phone_number: str) -> None:
        """Invalidate user profile cache."""
        key = f"cache:user:profile:{phone_number}"
        await self.redis.delete(key)

    # ============ Accounts Cache ============

    async def get_accounts(self, phone_number: str) -> list[dict[str, Any]] | None:
        """Get cached user accounts."""
        key = f"cache:user:accounts:{phone_number}"
        data = await self.redis.get(key)

        if data:
            return json.loads(data)
        return None

    async def set_accounts(self, phone_number: str, accounts: list[dict[str, Any]]) -> None:
        """Cache user accounts."""
        key = f"cache:user:accounts:{phone_number}"
        await self.redis.set(key, json.dumps(accounts), ex=self.ACCOUNTS_TTL)

    async def invalidate_accounts(self, phone_number: str) -> None:
        """Invalidate accounts cache."""
        key = f"cache:user:accounts:{phone_number}"
        await self.redis.delete(key)

    # ============ Beneficiaries Cache ============

    async def get_beneficiaries(self, phone_number: str) -> list[dict[str, Any]] | None:
        """Get cached beneficiaries."""
        key = f"cache:user:beneficiaries:{phone_number}"
        data = await self.redis.get(key)

        if data:
            return json.loads(data)
        return None

    async def set_beneficiaries(
        self, phone_number: str, beneficiaries: list[dict[str, Any]]
    ) -> None:
        """Cache beneficiaries."""
        key = f"cache:user:beneficiaries:{phone_number}"
        await self.redis.set(key, json.dumps(beneficiaries), ex=self.BENEFICIARIES_TTL)

    async def invalidate_beneficiaries(self, phone_number: str) -> None:
        """Invalidate beneficiaries cache."""
        key = f"cache:user:beneficiaries:{phone_number}"
        await self.redis.delete(key)

    # ============ Bulk Operations ============

    async def invalidate_all_user_data(self, phone_number: str) -> None:
        """Invalidate all cached data for a user."""
        await self.redis.delete(
            f"cache:user:profile:{phone_number}",
            f"cache:user:accounts:{phone_number}",
            f"cache:user:beneficiaries:{phone_number}",
        )

    async def get_all_user_data(self, phone_number: str) -> dict[str, Any | None]:
        """Get all cached user data in one call."""
        profile, accounts, beneficiaries = await self.redis.mget(
            f"cache:user:profile:{phone_number}",
            f"cache:user:accounts:{phone_number}",
            f"cache:user:beneficiaries:{phone_number}",
        )

        return {
            "profile": json.loads(profile) if profile else None,
            "accounts": json.loads(accounts) if accounts else None,
            "beneficiaries": json.loads(beneficiaries) if beneficiaries else None,
        }

    # ============ Cache-Aside Pattern Helpers ============

    async def get_or_set_profile(
        self,
        phone_number: str,
        fetch_fn,  # async callable that fetches from DB
    ) -> dict[str, Any] | None:
        """
        Get profile from cache or fetch from DB and cache.

        Cache-aside pattern implementation.
        """
        # Try cache first
        cached = await self.get_user_profile(phone_number)
        if cached is not None:
            return cached

        # Cache miss - fetch from DB
        profile = await fetch_fn(phone_number)

        # Cache the result (even if None, to prevent repeated DB queries)
        if profile:
            await self.set_user_profile(phone_number, profile)

        return profile

    async def get_or_set_accounts(
        self,
        phone_number: str,
        fetch_fn,  # async callable that fetches from DB
    ) -> list[dict[str, Any]]:
        """Get accounts from cache or fetch from DB and cache."""
        # Try cache first
        cached = await self.get_accounts(phone_number)
        if cached is not None:
            return cached

        # Cache miss - fetch from DB
        accounts = await fetch_fn(phone_number)

        # Cache the result
        if accounts:
            await self.set_accounts(phone_number, accounts)

        return accounts or []

    async def get_or_set_beneficiaries(
        self,
        phone_number: str,
        fetch_fn,  # async callable that fetches from DB
    ) -> list[dict[str, Any]]:
        """Get beneficiaries from cache or fetch from DB and cache."""
        # Try cache first
        cached = await self.get_beneficiaries(phone_number)
        if cached is not None:
            return cached

        # Cache miss - fetch from DB
        beneficiaries = await fetch_fn(phone_number)

        # Cache the result
        if beneficiaries:
            await self.set_beneficiaries(phone_number, beneficiaries)

        return beneficiaries or []

    # ============ Stats ============

    async def get_cache_stats(self, phone_number: str) -> dict[str, bool]:
        """Check which data is currently cached for a user."""
        keys = await self.redis.exists(
            f"cache:user:profile:{phone_number}",
            f"cache:user:accounts:{phone_number}",
            f"cache:user:beneficiaries:{phone_number}",
        )

        return {
            "profile_cached": keys >= 1,
            "accounts_cached": keys >= 2,
            "beneficiaries_cached": keys >= 3,
        }
