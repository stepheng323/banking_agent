from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transfers.models.types import TransferContext, TransferGates, TransferPayload
from banking.transfers.nodes.confirmation import ConfirmationStep
from banking.transfers.nodes.execution import ExecutionStep
from banking.transfers.nodes.security import AuthorizationStep


class _StubRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls.append((key, ttl, value))


def _payload() -> TransferPayload:
    return TransferPayload(
        amount=5000.0,
        recipient_name="Tolu Adebayo",
        recipient_account="2010000001",
        recipient_bank_name="Access Bank",
        source_account_id="acc-1",
        source_bank_name="Access Bank",
        source_account_name="Gaines",
        source_account_number="1234500003",
        idempotency_key="transfer-test-token",
    )


def _context() -> TransferContext:
    return TransferContext(
        phone_number="2348162511023",
        language="en",
        channel="telegram",
        beneficiaries=[],
        accounts=[],
        all_accounts=[],
    )


@pytest.mark.asyncio
async def test_transfer_confirmation_falls_back_to_global_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _StubRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls, redis_url=None: redis))

    result = await ConfirmationStep().execute(
        _payload(),
        _context(),
        TransferGates(pin_verified=False, confirmation_confirmed=False),
        SimpleNamespace(redis_client=None, user_id=None, transaction_repo=None),
    )

    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert redis.calls == [
        ("transfer:token:transfer-test-token:phone", 3600, "2348162511023"),
    ]


@pytest.mark.asyncio
async def test_transfer_authorization_request_falls_back_to_global_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _StubRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls, redis_url=None: redis))

    result = await AuthorizationStep().execute(
        _payload(),
        _context(),
        TransferGates(pin_verified=False, confirmation_confirmed=True),
        SimpleNamespace(redis_client=None),
    )

    assert result.outcome == TransactionOutcome.NEEDS_AUTH
    assert redis.calls == [
        ("transfer:token:transfer-test-token:phone", 3600, "2348162511023"),
    ]


@pytest.mark.asyncio
async def test_transfer_execution_rejects_unverified_pin_without_publishing() -> None:
    publisher = SimpleNamespace(publish=AsyncMock())

    result = await ExecutionStep().execute(
        _payload(),
        _context(),
        TransferGates(pin_verified=False, confirmation_confirmed=True),
        SimpleNamespace(publisher=publisher),
    )

    assert result.outcome == TransactionOutcome.NEEDS_AUTH
    publisher.publish.assert_not_called()
