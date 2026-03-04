import json

import pytest

from apps.core.src.agent.graphs.query.session import SESSION_TTL, QuerySessionManager


class _RedisStub:
    def __init__(self, payload: str | None) -> None:
        self.payload = payload
        self.expire_calls: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | None:
        del key
        return self.payload

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True


class _RedisExpireFails(_RedisStub):
    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_load_refreshes_ttl_on_success() -> None:
    key = "query:session:2348000000000"
    redis = _RedisStub(json.dumps({"session_active": True, "current_page": 1}))
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert loaded.get("session_active") is True
    assert redis.expire_calls == [(key, SESSION_TTL)]


@pytest.mark.asyncio
async def test_load_does_not_refresh_ttl_when_session_missing() -> None:
    redis = _RedisStub(None)
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load("query:session:2348000000001")

    assert loaded is None
    assert redis.expire_calls == []


@pytest.mark.asyncio
async def test_load_expire_failure_is_non_fatal() -> None:
    key = "query:session:2348000000002"
    redis = _RedisExpireFails(json.dumps({"session_active": True}))
    manager = QuerySessionManager(redis)  # type: ignore[arg-type]

    loaded = await manager.load(key)

    assert isinstance(loaded, dict)
    assert loaded.get("session_active") is True
    assert redis.expire_calls == [(key, SESSION_TTL)]
