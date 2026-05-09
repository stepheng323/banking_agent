from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.chat.src.agent.executors.airtime import AirtimeExecutor
from shared.database.enums import TransactionStatusEnum


class _RedisStub:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


def _payload() -> dict:
    return {
        "transaction_id": "tx-1",
        "idempotency_key": "idem-1",
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_identity": "927331985",
        "airtime_data": {
            "amount": 2000,
            "phone_number": "08031234567",
            "network": "MTN",
            "source_account_id": "acc-1",
        },
        "language": "en",
    }


@pytest.mark.asyncio
async def test_airtime_executor_success_delivers_to_channel_identity_not_recipient_phone() -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(return_value={"success": True, "reference": "ref-1"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_airtime(_payload())

    assert transaction_repo.update_status.await_args_list[0].args == ("tx-1", TransactionStatusEnum.PROCESSING.value)
    assert transaction_repo.update_status.await_args_list[1].args == ("tx-1", TransactionStatusEnum.SUCCESSFUL.value)
    assert delivery_service.deliver_text.await_args.kwargs["phone_number"] == "927331985"
    assert delivery_service.deliver_text.await_args.kwargs["channel"] == "telegram"
    assert "08031234567" in delivery_service.deliver_text.await_args.kwargs["text"]
    assert "MTN" in delivery_service.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_airtime_executor_failure_delivers_to_originating_user() -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(return_value={"success": False, "message": "Provider down"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_airtime(_payload())

    assert transaction_repo.update_status.await_args_list[0].args == ("tx-1", TransactionStatusEnum.PROCESSING.value)
    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.FAILED.value,
    )
    assert delivery_service.deliver_text.await_args.kwargs["phone_number"] == "927331985"
    assert "Provider down" in delivery_service.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_airtime_executor_grouped_success_waits_for_batch_summary() -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(return_value={"success": True, "reference": "ref-1"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    payload = _payload()
    payload["async_group"] = {
        "async_group_id": "group-airtime",
        "async_group_size": 2,
        "async_group_kind": "mixed_batch",
        "async_group_index": 1,
    }
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_airtime(payload)

    delivery_service.deliver_text.assert_not_awaited()
