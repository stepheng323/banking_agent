import time

import pytest

from banking.transactions.query.session import SESSION_TTL, QuerySessionManager, is_query_session_stale


class _RedisDeleteStub:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.deleted_keys: list[str] = []

    async def delete(self, key: str) -> int:
        self.deleted_keys.append(key)
        if self.fail:
            raise RuntimeError("boom")
        return 1


def test_query_session_stale_detects_expired_snapshot() -> None:
    session = {"timestamp": time.time() - SESSION_TTL - 1}

    assert is_query_session_stale(session)


def test_query_session_stale_keeps_snapshot_without_timestamp() -> None:
    assert is_query_session_stale({}) is False


def test_query_session_stale_treats_invalid_timestamp_as_stale() -> None:
    assert is_query_session_stale({"timestamp": "not-a-time"})


@pytest.mark.asyncio
async def test_query_session_manager_only_clears_legacy_redis_key() -> None:
    redis = _RedisDeleteStub()
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    await manager.clear("query:session:2348000000000")

    assert redis.deleted_keys == ["query:session:2348000000000"]


@pytest.mark.asyncio
async def test_query_session_manager_clear_failure_is_non_fatal() -> None:
    redis = _RedisDeleteStub(fail=True)
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    await manager.clear("query:session:2348000000000")

    assert redis.deleted_keys == ["query:session:2348000000000"]
