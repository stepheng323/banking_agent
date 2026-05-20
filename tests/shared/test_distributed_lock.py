import asyncio
from typing import Any

import pytest

from shared.cache.distributed_lock import RedisDistributedLock, RedisLockTimeoutError


class _RedisLockStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expires: dict[str, int] = {}
        self.set_calls: list[dict[str, Any]] = []
        self.set_results: list[bool] = []
        self.renew_count = 0

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        self.set_calls.append({"key": key, "value": value, "ex": ex, "nx": nx})
        if self.set_results:
            allowed = self.set_results.pop(0)
            if not allowed:
                return False
        elif nx and key in self.values:
            return False

        self.values[key] = value
        self.expires[key] = ex
        return True

    async def eval(self, script: str, _numkeys: int, key: str, token: str, *args: str) -> int:
        if self.values.get(key) != token:
            return 0
        if "expire" in script:
            self.renew_count += 1
            self.expires[key] = int(args[0])
            return 1
        if "del" in script:
            del self.values[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_distributed_lock_acquire_uses_set_nx_ex() -> None:
    redis = _RedisLockStub()
    lock = RedisDistributedLock(redis, key="chat:thread-lock:whatsapp:1", ttl_seconds=120, token="token-a")

    acquired = await lock.acquire(wait_seconds=0)

    assert acquired is True
    assert redis.values["chat:thread-lock:whatsapp:1"] == "token-a"
    assert redis.set_calls == [{"key": "chat:thread-lock:whatsapp:1", "value": "token-a", "ex": 120, "nx": True}]


@pytest.mark.asyncio
async def test_distributed_lock_waits_until_available() -> None:
    redis = _RedisLockStub()
    redis.set_results = [False, False, True]
    lock = RedisDistributedLock(redis, key="chat:thread-lock:telegram:2", ttl_seconds=120, token="token-b")

    acquired = await lock.acquire(wait_seconds=1, retry_interval_seconds=0.001)

    assert acquired is True
    assert len(redis.set_calls) == 3


@pytest.mark.asyncio
async def test_distributed_lock_times_out_when_busy() -> None:
    redis = _RedisLockStub()
    redis.values["chat:thread-lock:whatsapp:3"] = "other-token"
    lock = RedisDistributedLock(redis, key="chat:thread-lock:whatsapp:3", ttl_seconds=120, token="token-c")

    with pytest.raises(RedisLockTimeoutError):
        await lock.acquire(wait_seconds=0.001, retry_interval_seconds=0.001)


@pytest.mark.asyncio
async def test_distributed_lock_renews_and_releases_by_token() -> None:
    redis = _RedisLockStub()
    lock = RedisDistributedLock(redis, key="chat:thread-lock:whatsapp:4", ttl_seconds=120, token="token-d")
    await lock.acquire(wait_seconds=0)

    assert await lock.renew() is True
    assert redis.expires["chat:thread-lock:whatsapp:4"] == 120
    assert await lock.release() is True
    assert "chat:thread-lock:whatsapp:4" not in redis.values


@pytest.mark.asyncio
async def test_distributed_lock_release_is_token_safe() -> None:
    redis = _RedisLockStub()
    redis.values["chat:thread-lock:whatsapp:5"] = "other-token"
    lock = RedisDistributedLock(redis, key="chat:thread-lock:whatsapp:5", ttl_seconds=120, token="token-e")

    assert await lock.release() is False
    assert redis.values["chat:thread-lock:whatsapp:5"] == "other-token"


@pytest.mark.asyncio
async def test_distributed_lock_periodic_renewal_stops_on_cancel() -> None:
    redis = _RedisLockStub()
    lock = RedisDistributedLock(redis, key="chat:thread-lock:whatsapp:6", ttl_seconds=120, token="token-f")
    await lock.acquire(wait_seconds=0)

    task = asyncio.create_task(lock.renew_periodically(interval_seconds=0.001))
    await asyncio.sleep(0.003)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert redis.renew_count > 0
    assert redis.expires["chat:thread-lock:whatsapp:6"] == 120
