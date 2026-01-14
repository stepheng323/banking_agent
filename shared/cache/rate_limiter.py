"""Rate limiting utilities for fault tolerance.

Provides per-user rate limiting to prevent:
- Spam attacks
- Resource exhaustion
- LLM cost escalation
"""

import time
from dataclasses import dataclass

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_REQUESTS = 10
DEFAULT_WINDOW_SECONDS = 60


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    remaining: int
    reset_in_seconds: int
    total_limit: int


class RateLimiter:
    """
    Redis-based rate limiter using sliding window algorithm.

    Usage:
        limiter = RateLimiter()
        result = await limiter.check("user:2348162511023")
        if not result.allowed:
            return "Too many requests. Please wait."
    """

    def __init__(
        self,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        key_prefix: str = "ratelimit",
    ):
        """
        Initialize rate limiter.

        Args:
            max_requests: Maximum requests allowed per window
            window_seconds: Window duration in seconds
            key_prefix: Redis key prefix
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _get_key(self, identifier: str) -> str:
        """Get Redis key for identifier."""
        return f"{self.key_prefix}:{identifier}"

    async def check(self, identifier: str) -> RateLimitResult:
        """
        Check if request is allowed and increment counter.

        Args:
            identifier: Unique identifier (e.g., phone number, user ID)

        Returns:
            RateLimitResult with allowed status and metadata
        """
        try:
            redis = RedisClient.get_client()
            key = self._get_key(identifier)
            now = time.time()
            window_start = now - self.window_seconds

            await redis.zremrangebyscore(key, 0, window_start)

            current_count = await redis.zcard(key)

            if current_count >= self.max_requests:
                oldest = await redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    oldest_time = oldest[0][1]
                    reset_in = int(oldest_time + self.window_seconds - now)
                else:
                    reset_in = self.window_seconds

                logger.warning(
                    "rate_limit_exceeded",
                    identifier=identifier,
                    current_count=current_count,
                    reset_in=reset_in,
                )

                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_in_seconds=max(1, reset_in),
                    total_limit=self.max_requests,
                )

            await redis.zadd(key, {str(now): now})

            await redis.expire(key, self.window_seconds + 10)

            remaining = self.max_requests - current_count - 1

            return RateLimitResult(
                allowed=True,
                remaining=remaining,
                reset_in_seconds=self.window_seconds,
                total_limit=self.max_requests,
            )

        except Exception as e:
            logger.error("rate_limiter_error", error=str(e), identifier=identifier)
            return RateLimitResult(
                allowed=True,
                remaining=self.max_requests,
                reset_in_seconds=self.window_seconds,
                total_limit=self.max_requests,
            )

    async def reset(self, identifier: str) -> None:
        """
        Reset rate limit for an identifier.

        Args:
            identifier: Unique identifier to reset
        """
        try:
            redis = RedisClient.get_client()
            key = self._get_key(identifier)
            await redis.delete(key)
        except Exception as e:
            logger.error("rate_limiter_reset_error", error=str(e))


message_rate_limiter = RateLimiter(
    max_requests=10,
    window_seconds=60,
    key_prefix="ratelimit:messages",
)
