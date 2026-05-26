from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.nodes.confirmation import ConfirmationStep


class _StubRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls.append((key, ttl, value))


@pytest.mark.asyncio
async def test_data_confirmation_persists_data_and_generic_transaction_tokens() -> None:
    redis = _StubRedis()
    step = ConfirmationStep()
    payload = DataPayload(
        amount=3500.0,
        network="MTN",
        target_phone="08162511023",
        plan_code="MD501",
        plan_name="MTN 5 GB data bundle",
        biller_code="BIL104",
        source_account_id="acc-1",
        source_bank_name="Zenith Bank",
        source_account_name="Olamide Samuel",
        source_account_number="1234509384",
        idempotency_key="data-test-token",
    )
    context = DataContext(
        phone_number="2348162511023",
        language="en",
        channel="telegram",
        beneficiaries=[],
        accounts=[],
        all_accounts=[],
    )
    gates = DataGates(pin_verified=False, confirmation_confirmed=False)
    worker_context = SimpleNamespace(redis_client=redis)

    result = await step.run(payload, context, gates, worker_context)

    assert result is not None
    assert result.outcome.value == "needs_confirmation"
    assert redis.calls == [
        ("data:token:data-test-token:phone", 3600, "2348162511023"),
        ("transaction:token:data-test-token:phone", 3600, "2348162511023"),
    ]


@pytest.mark.asyncio
async def test_data_confirmation_falls_back_to_global_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _StubRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls, redis_url=None: redis))

    step = ConfirmationStep()
    payload = DataPayload(
        amount=3500.0,
        network="MTN",
        target_phone="08162511023",
        plan_code="MD501",
        plan_name="MTN 5 GB data bundle",
        source_account_id="acc-1",
        source_bank_name="Zenith Bank",
        source_account_name="Olamide Samuel",
        source_account_number="1234509384",
        idempotency_key="data-test-token",
    )
    context = DataContext(phone_number="2348162511023", language="en", channel="telegram")
    gates = DataGates(pin_verified=False, confirmation_confirmed=False)
    worker_context = SimpleNamespace(redis_client=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result is not None
    assert result.outcome.value == "needs_confirmation"
    assert redis.calls == [
        ("data:token:data-test-token:phone", 3600, "2348162511023"),
        ("transaction:token:data-test-token:phone", 3600, "2348162511023"),
    ]
