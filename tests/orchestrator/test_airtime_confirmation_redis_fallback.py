from types import SimpleNamespace

import pytest

from banking.bills.airtime.models.types import AirtimeContext, AirtimeGates, AirtimePayload
from banking.bills.airtime.nodes.confirmation import ConfirmationStep


class _StubRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls.append((key, ttl, value))


@pytest.mark.asyncio
async def test_airtime_confirmation_without_redis_skips_token_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _StubRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(
        RedisClient,
        "get_client",
        classmethod(lambda cls, redis_url=None: (_ for _ in ()).throw(AssertionError("global Redis is not allowed"))),
    )

    step = ConfirmationStep()
    payload = AirtimePayload(
        amount=1000.0,
        recipient_phone="08162511023",
        network="MTN",
        recipient_name=None,
        beneficiary_id=None,
        is_self=True,
        source_account_id="acc-1",
        source_bank_name="Zenith Bank",
        source_account_name="Olamide Samuel",
        source_account_number="1234509384",
        source_account_index=None,
        idempotency_key="airtime-test-token",
        transaction_id=None,
        narration=None,
        correction_field=None,
        correction_value=None,
    )
    context = AirtimeContext(
        phone_number="2348162511023",
        language="en",
        channel="telegram",
        beneficiaries=[],
        accounts=[],
        all_accounts=[],
    )
    gates = AirtimeGates(pin_verified=False, confirmation_confirmed=False)
    worker_context = SimpleNamespace(redis_client=None)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome.value == "needs_confirmation"
    assert result.confirmation_summary is not None
    assert result.confirmation_summary.splitlines()[0] == "*₦1,000 airtime for your number (08162511023)*"
    assert redis.calls == []
