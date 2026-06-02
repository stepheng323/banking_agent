import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.policy.loader import get_cached_policy, load_policy
from banking.transactions.runtime.executors import data as data_module
from banking.transactions.runtime.executors.data import DataExecutor
from shared.database.enums import TransactionStatusEnum

CAPABILITY_POLICY_PATH = "banking/policy/defaults/capability_policy.json"
DATA_DISABLED_MESSAGE = (
    "Data purchase is temporarily unavailable. I can still help with transfers, airtime, balances, and transaction "
    "queries."
)


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


class _SuggestionServiceStub:
    def __init__(self, message: str | None = "Would you like to save this MTN line?") -> None:
        self.check_and_suggest_beneficiary = AsyncMock(return_value=message)


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
        "data_purchase": {
            "plan_code": "mtn-1gb",
            "plan_name": "1GB Weekly",
            "amount": 1500,
            "target_phone": "08031234567",
            "network": "MTN",
            "recipient_name": "Tolu",
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


def _install_disabled_data_policy(tmp_path: Path) -> None:
    raw = load_policy(CAPABILITY_POLICY_PATH).model_dump()
    raw["capability_matrix"]["data"]["enabled"] = False
    raw["capability_matrix"]["data"]["limitation_message"] = DATA_DISABLED_MESSAGE
    policy_path = tmp_path / "capability_policy_data_disabled.json"
    policy_path.write_text(json.dumps(raw, ensure_ascii=True), encoding="utf-8")
    get_cached_policy(path=str(policy_path), force_reload=True)


def _reset_policy_cache() -> None:
    get_cached_policy(path=CAPABILITY_POLICY_PATH, force_reload=True)


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

    monkeypatch.setattr(data_module, "UnitOfWork", _Uow)


@pytest.mark.asyncio
async def test_data_executor_always_queues_mono_debit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    publisher = _PublisherStub()
    tx = SimpleNamespace(id="tx-1", idempotency_key="idem-1", amount=1500, currency="NGN")
    debit_steps = SimpleNamespace(get_or_create_for_transaction=AsyncMock(return_value=(SimpleNamespace(), True)))
    payload = _payload()
    payload["data_purchase"]["source_account_id"] = "acc-1"
    _install_uow(monkeypatch, tx, debit_steps)

    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        publisher=publisher,
    )

    await executor.handle_data(payload)

    provider.purchase_data.assert_not_awaited()
    debit_steps.get_or_create_for_transaction.assert_awaited_once_with(
        transaction=tx,
        account_id="acc-1",
        provider_reference="idem-1-debit",
        provider_name="mono",
    )
    assert publisher.messages == [
        ("transaction_debit.process", {"transaction_id": "tx-1", "idempotency_key": "idem-1"})
    ]
    assert tx.service_metadata["completion_context"] == {
        "domain": "data",
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_identity": "927331985",
        "language": "en",
        "async_group": {
            "async_group_id": "group-1",
            "async_group_size": 1,
            "async_group_kind": "single",
            "async_group_index": 1,
        },
        "amount_naira": "1500.00",
        "plan_code": "mtn-1gb",
        "plan_name": "1GB Weekly",
        "target_phone": "08031234567",
        "network": "MTN",
        "recipient_name": "Tolu",
        "source": "****7890",
        "source_account_id": "acc-1",
        "source_account_number": "****7890",
    }
    delivery_service.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_executor_missing_source_account_fails_before_debit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(purchase_data=AsyncMock(return_value={"success": True}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    publisher = _PublisherStub()
    payload = _payload()
    payload["data_purchase"]["source_account_id"] = None
    _install_uow(
        monkeypatch,
        SimpleNamespace(id="tx-1", idempotency_key="idem-1", amount=1500, currency="NGN"),
        SimpleNamespace(get_or_create_for_transaction=AsyncMock()),
    )

    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=SimpleNamespace(deliver_text=AsyncMock()),
        redis_client=_RedisStub(),
        publisher=publisher,
    )

    await executor.handle_data(payload)

    provider.purchase_data.assert_not_awaited()
    assert publisher.messages == []
    transaction_repo.update_status.assert_any_await(
        "tx-1",
        TransactionStatusEnum.FAILED.value,
        error_message="Source account missing for data purchase",
    )


@pytest.mark.asyncio
async def test_data_executor_policy_block_does_not_call_provider(tmp_path: Path) -> None:
    _install_disabled_data_policy(tmp_path)
    try:
        provider = SimpleNamespace(purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"}))
        transaction_repo = SimpleNamespace(update_status=AsyncMock())
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        payload = _payload()
        payload.pop("async_group")
        executor = DataExecutor(
            bill_provider=provider,
            transaction_repo=transaction_repo,
            delivery_service=delivery_service,
            redis_client=_RedisStub(),
        )

        await executor.handle_data(payload)

        provider.purchase_data.assert_not_awaited()
        assert transaction_repo.update_status.await_args_list[0].args == (
            "tx-1",
            TransactionStatusEnum.FAILED.value,
        )
        assert transaction_repo.update_status.await_args_list[0].kwargs["error_message"] == DATA_DISABLED_MESSAGE
        assert DATA_DISABLED_MESSAGE in delivery_service.deliver_text.await_args.kwargs["text"]
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_data_executor_missing_plan_code_does_not_call_provider() -> None:
    provider = SimpleNamespace(purchase_data=AsyncMock(return_value={"success": True}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    payload = _payload()
    payload["data_purchase"]["plan_code"] = None
    payload.pop("async_group")
    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_data(payload)

    provider.purchase_data.assert_not_awaited()
    assert transaction_repo.update_status.await_args_list[1].args == ("tx-1", TransactionStatusEnum.FAILED.value)
    assert (
        transaction_repo.update_status.await_args_list[1].kwargs["error_message"]
        == "Please choose a valid data plan before we continue."
    )
