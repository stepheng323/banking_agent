"""Redis-backed distributed lock primitives."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable
from inspect import isawaitable
from typing import Any, TypeVar, cast

_T = TypeVar("_T")

_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class RedisLockTimeoutError(TimeoutError):
    """Raised when a Redis distributed lock cannot be acquired before its deadline."""


async def _await_maybe(value: _T | Awaitable[_T]) -> _T:
    if isawaitable(value):
        return await cast(Awaitable[_T], value)
    return value


class RedisDistributedLock:
    """Token-safe Redis lock using SET NX EX and Lua compare-and-delete release."""

    def __init__(
        self,
        redis_client: Any,
        *,
        key: str,
        ttl_seconds: int,
        token: str | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.redis = redis_client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.token = token or secrets.token_urlsafe(24)

    async def acquire(self, *, wait_seconds: float, retry_interval_seconds: float = 0.1) -> bool:
        """Acquire the lock, waiting up to wait_seconds before raising."""
        deadline = time.monotonic() + max(0.0, wait_seconds)

        while True:
            acquired = await _await_maybe(
                self.redis.set(
                    self.key,
                    self.token,
                    ex=self.ttl_seconds,
                    nx=True,
                )
            )
            if bool(acquired):
                return True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RedisLockTimeoutError(f"redis_lock_acquire_timeout:{self.key}")

            await asyncio.sleep(min(retry_interval_seconds, remaining))

    async def renew(self) -> bool:
        """Renew the lock TTL only when this instance still owns the token."""
        result = await _await_maybe(
            self.redis.eval(
                _RENEW_SCRIPT,
                1,
                self.key,
                self.token,
                str(self.ttl_seconds),
            )
        )
        return bool(result)

    async def release(self) -> bool:
        """Release the lock only when this instance still owns the token."""
        result = await _await_maybe(
            self.redis.eval(
                _RELEASE_SCRIPT,
                1,
                self.key,
                self.token,
            )
        )
        return bool(result)

    async def renew_periodically(self, *, interval_seconds: float) -> None:
        """Renew the lock until cancelled."""
        interval = interval_seconds if interval_seconds > 0 else 0.1
        while True:
            await asyncio.sleep(interval)
            if not await self.renew():
                raise RuntimeError(f"redis_lock_renew_failed:{self.key}")
