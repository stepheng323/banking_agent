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

    PROFILE_TTL = int(timedelta(minutes=10).total_seconds())
    ACCOUNTS_TTL = int(timedelta(minutes=5).total_seconds())
    BENEFICIARIES_TTL = int(timedelta(minutes=5).total_seconds())

    def __init__(self, redis_client: Redis | None = None):
        self.redis = redis_client or RedisClient.get_client()

    @staticmethod
    def _build_beneficiary_alias_map(beneficiaries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        alias_map: dict[str, dict[str, Any]] = {}
        for beneficiary in beneficiaries:
            name = (beneficiary.get("name") or "").lower().strip()
            alias = (beneficiary.get("alias") or "").lower().strip()
            if name:
                alias_map[name] = beneficiary
            if alias:
                alias_map[alias] = beneficiary
        return alias_map

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

    async def get_beneficiaries(self, phone_number: str) -> list[dict[str, Any]] | None:
        """Get cached beneficiaries."""
        key = f"cache:user:beneficiaries:{phone_number}"
        data = await self.redis.get(key)

        if data:
            return json.loads(data)
        return None

    async def set_beneficiaries(self, phone_number: str, beneficiaries: list[dict[str, Any]]) -> None:
        """Cache beneficiaries and build alias lookup map."""
        key = f"cache:user:beneficiaries:{phone_number}"
        await self.redis.set(key, json.dumps(beneficiaries), ex=self.BENEFICIARIES_TTL)

        alias_map = self._build_beneficiary_alias_map(beneficiaries)
        if alias_map:
            alias_key = f"cache:user:beneficiary_aliases:{phone_number}"
            await self.redis.set(alias_key, json.dumps(alias_map), ex=self.BENEFICIARIES_TTL)

    async def set_user_data_snapshot(
        self,
        phone_number: str,
        *,
        profile: dict[str, Any] | None = None,
        cache_profile: bool = False,
        accounts: list[dict[str, Any]] | None = None,
        cache_accounts: bool = False,
        beneficiaries: list[dict[str, Any]] | None = None,
        cache_beneficiaries: bool = False,
    ) -> None:
        """Write multiple user-data cache keys in one Redis pipeline."""
        pipe = self.redis.pipeline()

        if cache_profile:
            pipe.set(
                f"cache:user:profile:{phone_number}",
                json.dumps(profile),
                ex=self.PROFILE_TTL,
            )

        if cache_accounts:
            pipe.set(
                f"cache:user:accounts:{phone_number}",
                json.dumps(accounts),
                ex=self.ACCOUNTS_TTL,
            )

        if cache_beneficiaries:
            beneficiaries_value = beneficiaries or []
            pipe.set(
                f"cache:user:beneficiaries:{phone_number}",
                json.dumps(beneficiaries_value),
                ex=self.BENEFICIARIES_TTL,
            )
            alias_key = f"cache:user:beneficiary_aliases:{phone_number}"
            alias_map = self._build_beneficiary_alias_map(beneficiaries_value)
            if alias_map:
                pipe.set(alias_key, json.dumps(alias_map), ex=self.BENEFICIARIES_TTL)
            else:
                pipe.delete(alias_key)

        await pipe.execute()

    async def get_beneficiary_by_alias(self, phone_number: str, alias: str) -> dict[str, Any] | None:
        """
        O(1) beneficiary lookup by name or alias.

        Args:
            phone_number: User's phone number
            alias: Name or alias to search for (case-insensitive)

        Returns:
            Beneficiary dict if found, None otherwise
        """
        alias_key = f"cache:user:beneficiary_aliases:{phone_number}"
        data = await self.redis.get(alias_key)
        if data:
            alias_map = json.loads(data)
            return alias_map.get(alias.lower().strip())
        return None

    async def invalidate_beneficiaries(self, phone_number: str) -> None:
        """Invalidate beneficiaries cache and alias map."""
        await self.redis.delete(
            f"cache:user:beneficiaries:{phone_number}",
            f"cache:user:beneficiary_aliases:{phone_number}",
        )

    async def invalidate_all_user_data(self, phone_number: str) -> None:
        """Invalidate all cached data for a user."""
        await self.redis.delete(
            f"cache:user:profile:{phone_number}",
            f"cache:user:accounts:{phone_number}",
            f"cache:user:beneficiaries:{phone_number}",
            f"cache:user:beneficiary_aliases:{phone_number}",
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

    async def get_or_set_profile(
        self,
        phone_number: str,
        fetch_fn,  # async callable that fetches from DB
    ) -> dict[str, Any] | None:
        """
        Get profile from cache or fetch from DB and cache.

        Cache-aside pattern implementation.
        """
        cached = await self.get_user_profile(phone_number)
        if cached is not None:
            return cached

        profile = await fetch_fn(phone_number)

        if profile:
            await self.set_user_profile(phone_number, profile)

        return profile

    async def get_or_set_accounts(
        self,
        phone_number: str,
        fetch_fn,
    ) -> list[dict[str, Any]]:
        """Get accounts from cache or fetch from DB and cache."""
        cached = await self.get_accounts(phone_number)
        if cached is not None:
            return cached

        accounts = await fetch_fn(phone_number)

        if accounts:
            await self.set_accounts(phone_number, accounts)

        return accounts or []

    async def get_or_set_beneficiaries(
        self,
        phone_number: str,
        fetch_fn,
    ) -> list[dict[str, Any]]:
        """Get beneficiaries from cache or fetch from DB and cache."""
        cached = await self.get_beneficiaries(phone_number)
        if cached is not None:
            return cached

        beneficiaries = await fetch_fn(phone_number)

        if beneficiaries:
            await self.set_beneficiaries(phone_number, beneficiaries)

        return beneficiaries or []

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
