from typing import Any

import pytest

from shared.services.auth import AuthorizationService


class _RedisStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[dict[str, Any]] = []

    async def set(self, key: str, value: str, **kwargs: Any) -> bool | None:
        self.set_calls.append({"key": key, "value": value, **kwargs})
        if kwargs.get("nx") and key in self.values:
            return None
        self.values[key] = value
        return True


@pytest.mark.asyncio
async def test_claim_pin_resume_uses_one_time_redis_claim() -> None:
    redis = _RedisStub()
    service = AuthorizationService(redis_client=redis)

    first = await service.claim_pin_resume("idem-1", ttl_seconds=60)
    second = await service.claim_pin_resume("idem-1", ttl_seconds=60)

    assert first is True
    assert second is False
    assert redis.set_calls == [
        {
            "key": "transaction:pin_resume_claim:idem-1",
            "value": "1",
            "ex": 60,
            "nx": True,
        },
        {
            "key": "transaction:pin_resume_claim:idem-1",
            "value": "1",
            "ex": 60,
            "nx": True,
        },
    ]
