from typing import Any

import pytest

from banking.security.authorization import AuthorizationService
from shared.utils.hash import is_valid_pin_format


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


def test_transaction_pin_format_requires_six_digits() -> None:
    assert is_valid_pin_format("123456") is True
    assert is_valid_pin_format("1234") is False
    assert is_valid_pin_format("1234567") is False
    assert is_valid_pin_format("12345a") is False


@pytest.mark.asyncio
async def test_verify_pin_rejects_four_digit_pin_before_session_lookup() -> None:
    service = AuthorizationService(redis_client=_RedisStub())

    result = await service.verify_pin("2348162511023", "1234", "idem-1", transaction_type="transfer")

    assert result.verified is False
    assert result.error == "Invalid PIN. Enter a 6-digit numeric PIN."
    assert result.retry_count == 0


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
