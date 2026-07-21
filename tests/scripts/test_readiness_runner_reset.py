import pytest

from scripts.readiness_runner import reset_redis_session


class _RedisResetStub:
    def __init__(self) -> None:
        self.scanned_patterns: list[str] = []
        self.deleted_keys: list[str] = []

    async def scan_iter(self, match: str):
        self.scanned_patterns.append(match)
        yield f"{match}:key"

    async def delete(self, *keys: str) -> int:
        self.deleted_keys.extend(keys)
        return len(keys)


@pytest.mark.asyncio
async def test_reset_redis_session_clears_checkpoint_latest() -> None:
    redis = _RedisResetStub()

    deleted = await reset_redis_session(
        redis_client=redis,
        phone="2348000000001",
        channel="whatsapp",
        user_id="user-1",
    )

    assert deleted == 12
    assert "checkpoint_latest:whatsapp:2348000000001:*" in redis.scanned_patterns
    assert "checkpoint_ttl_refresh:whatsapp:2348000000001" in redis.scanned_patterns
    assert "chat:thread-lock:whatsapp:2348000000001" in redis.scanned_patterns
    assert "user:2348000000001:*" in redis.scanned_patterns
    assert "support_context:2348000000001" in redis.scanned_patterns
    assert "support_context:user-1" in redis.scanned_patterns
    assert any(key.startswith("checkpoint_latest:whatsapp:2348000000001:") for key in redis.deleted_keys)
