from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.transactions.runtime.executors import airtime as airtime_module
from banking.transactions.runtime.executors.airtime import AirtimeExecutor
from shared.database.enums import TransactionStatusEnum


class _RedisStub:
    async def hset(self, key: str, field: str, value: str) -> None:
        return None

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def hlen(self, key: str) -> int:
        return 0

    async def hgetall(self, key: str) -> dict[str, str]:
        return {}


class _PublisherStub:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    async def publish(self, *, topic: str, message: dict) -> None:
        self.messages.append((topic, message))


def _payload() -> dict:
    return {
        "transaction_id": "tx-1",
        "idempotency_key": "idem-1",
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_identity": "927331985",
        "language": "en",
        "airtime_data": {
            "amount": 2000,
            "phone_number": "08031234567",
            "network": "MTN",
            "recipient_name": "Tolu",
            "source_account_id": "acc-1",
        },
    }


def _install_uow(monkeypatch: pytest.MonkeyPatch, tx: SimpleNamespace, debit_steps: SimpleNamespace) -> None:
    transactions = SimpleNamespace(get_by_id=AsyncMock(return_value=tx))

    class _Uow:
        def __init__(self) -> None:
            self.transactions = transactions
            self.transaction_debit_steps = debit_steps
            self.db = SimpleNamespace(add=lambda value: None)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(airtime_module, "UnitOfWork", _Uow)


@pytest.mark.asyncio
async def test_airtime_executor_always_queues_mono_debit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(return_value={"success": True, "reference": "ref-1"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    publisher = _PublisherStub()
    tx = SimpleNamespace(id="tx-1", idempotency_key="idem-1", amount=2000, currency="NGN")
    debit_steps = SimpleNamespace(get_or_create_for_transaction=AsyncMock(return_value=(SimpleNamespace(), True)))
    _install_uow(monkeypatch, tx, debit_steps)

    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=publisher,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_airtime(_payload())

    provider.purchase_airtime.assert_not_awaited()
    debit_steps.get_or_create_for_transaction.assert_awaited_once_with(
        transaction=tx,
        account_id="acc-1",
        provider_reference="idem-1-debit",
    )
    assert publisher.messages == [
        ("transaction_debit.process", {"transaction_id": "tx-1", "idempotency_key": "idem-1"})
    ]
    assert tx.service_metadata["completion_context"] == {
        "domain": "airtime",
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_identity": "927331985",
        "language": "en",
        "amount_naira": "2000.00",
        "recipient_phone": "08031234567",
        "network": "MTN",
        "recipient_name": "Tolu",
        "source_account_id": "acc-1",
    }
    delivery_service.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_airtime_executor_missing_source_account_fails_before_debit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock())
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    publisher = _PublisherStub()
    payload = _payload()
    payload["airtime_data"].pop("source_account_id")
    _install_uow(
        monkeypatch,
        SimpleNamespace(id="tx-1", idempotency_key="idem-1", amount=2000, currency="NGN"),
        SimpleNamespace(get_or_create_for_transaction=AsyncMock()),
    )

    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=publisher,
        delivery_service=SimpleNamespace(deliver_text=AsyncMock()),
        redis_client=_RedisStub(),
    )

    await executor.handle_airtime(payload)

    provider.purchase_airtime.assert_not_awaited()
    assert publisher.messages == []
    transaction_repo.update_status.assert_any_await(
        "tx-1",
        TransactionStatusEnum.FAILED.value,
        error_message="Source account missing for airtime purchase",
    )
