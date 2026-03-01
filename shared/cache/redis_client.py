"""Shared Redis client singleton."""

import redis.asyncio as redis

from shared.config.settings import settings

Redis = redis.Redis


class RedisClient:
    """Singleton Redis client for use across all cache services."""

    _instance: redis.Redis | None = None
    _redis_url: str | None = None

    @classmethod
    def get_client(cls, redis_url: str | None = None) -> redis.Redis:
        """
        Get or create the shared Redis client instance.

        Args:
            redis_url: Optional Redis URL. If not provided, uses settings.redis_url

        Returns:
            Shared Redis client instance
        """
        if cls._instance is None or (redis_url and redis_url != cls._redis_url):
            url = redis_url or settings.redis_url
            cls._instance = redis.from_url(
                url,
                encoding="utf-8",
                decode_responses=True,
            )
            cls._redis_url = url
        return cls._instance

    @classmethod
    def set_client(cls, client: redis.Redis) -> None:
        """
        Set a specific Redis client instance (useful for testing or explicit setup).

        Args:
            client: Redis client instance to use
        """
        cls._instance = client

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
        cls._redis_url = None
