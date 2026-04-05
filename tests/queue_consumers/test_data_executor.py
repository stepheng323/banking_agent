from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.core.src.agent.executors.data import DataExecutor
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
        "data_purchase": {
            "plan_code": "mtn-1gb",
            "plan_name": "1GB Weekly",
            "amount": 1500,
            "target_phone": "08031234567",
            "network": "MTN",
            "source": "1234567890",
        },
        "language": "en",
        "async_group": {
            "async_group_id": "group-1",
            "async_group_size": 1,
            "async_group_kind": "single",
            "async_group_index": 1,
        },
    }


@pytest.mark.asyncio
async def test_data_executor_single_success_delivers_to_originating_user() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_data(_payload())

    assert transaction_repo.update_status.await_args_list[0].args == ("tx-1", TransactionStatusEnum.PROCESSING.value)
    assert transaction_repo.update_status.await_args_list[1].args == ("tx-1", TransactionStatusEnum.SUCCESSFUL.value)
    assert delivery_service.deliver_text.await_args.kwargs["phone_number"] == "927331985"
    assert "Data purchase successful" in delivery_service.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_data_executor_grouped_failure_waits_for_batch_summary() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": False, "message": "Provider down"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    payload = _payload()
    payload["async_group"] = {
        "async_group_id": "group-data",
        "async_group_size": 2,
        "async_group_kind": "mixed_batch",
        "async_group_index": 1,
    }
    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_data(payload)

    assert transaction_repo.update_status.await_args_list[1].args == ("tx-1", TransactionStatusEnum.FAILED.value)
    delivery_service.deliver_text.assert_not_awaited()
