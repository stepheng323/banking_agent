import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared.database.enums import TransactionStatusEnum
from shared.policy.loader import get_cached_policy, load_policy
from banking.transactions.runtime.executors.data import DataExecutor

CAPABILITY_POLICY_PATH = "config/capability_policy.json"
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
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_transaction_id"] == "provider-1"
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_response"] == {
        "success": True,
        "transaction_id": "provider-1",
    }
    provider.purchase_data.assert_awaited_once_with(
        plan_code="mtn-1gb",
        recipient_phone="08031234567",
        network="MTN",
        amount=1500.0,
        reference="idem-1",
    )
    assert delivery_service.deliver_text.await_args.kwargs["phone_number"] == "927331985"
    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Data purchase successful" in text
    assert "Recipient: Tolu (MTN)" in text
    assert "Recipient: 1GB Weekly" not in text


@pytest.mark.asyncio
async def test_data_executor_success_uses_phone_as_recipient_when_name_missing() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    payload = _payload()
    payload["data_purchase"].pop("recipient_name")
    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_data(payload)

    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Data purchase successful" in text
    assert "Recipient: 08031234567 (MTN)" in text
    assert "Recipient: 1GB Weekly" not in text


@pytest.mark.asyncio
async def test_data_executor_single_success_appends_beneficiary_suggestion() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_data(_payload())

    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Data purchase successful" in text
    assert "Would you like to save this MTN line?" in text
    suggestion_service.check_and_suggest_beneficiary.assert_awaited_once()
    assert suggestion_service.check_and_suggest_beneficiary.await_args.kwargs["recipient_data"]["name"] == "Tolu"


@pytest.mark.asyncio
async def test_data_executor_self_success_does_not_suggest_beneficiary() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    payload = _payload()
    payload["data_purchase"]["target_phone"] = "08162511023"
    payload["data_purchase"]["recipient_name"] = ""
    payload["data_purchase"]["is_self"] = True
    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_data(payload)

    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Would you like to save" not in text
    suggestion_service.check_and_suggest_beneficiary.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_executor_grouped_success_does_not_suggest_beneficiary() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
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
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_data(payload)

    suggestion_service.check_and_suggest_beneficiary.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_data_executor_failure_uses_provider_error_field() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": False, "error": "Invalid data bundle"})
    )
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

    assert transaction_repo.update_status.await_args_list[1].args == ("tx-1", TransactionStatusEnum.FAILED.value)
    assert transaction_repo.update_status.await_args_list[1].kwargs["error_message"] == "Invalid data bundle"
    assert "Invalid data bundle" in delivery_service.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_data_executor_provider_pending_stays_processing_without_duplicate_delivery() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": False, "message": "Bill payment is Pending"})
    )
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

    assert transaction_repo.update_status.await_args_list[0].args == ("tx-1", TransactionStatusEnum.PROCESSING.value)
    assert transaction_repo.update_status.await_args_list[1].args == ("tx-1", TransactionStatusEnum.PROCESSING.value)
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_status"] == "pending"
    delivery_service.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_executor_provider_pending_sends_status_with_beneficiary_suggestion_when_available() -> None:
    provider = SimpleNamespace(
        purchase_data=AsyncMock(return_value={"success": False, "message": "Bill payment is Pending"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    payload = _payload()
    payload.pop("async_group")
    executor = DataExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_data(payload)

    delivery_service.deliver_text.assert_awaited_once()
    assert delivery_service.deliver_text.await_args.kwargs["text"] == (
        "This data purchase is still processing.\n\nWould you like to save this MTN line?"
    )


@pytest.mark.asyncio
async def test_data_executor_exception_uses_safe_user_error() -> None:
    provider = SimpleNamespace(purchase_data=AsyncMock(side_effect=RuntimeError("raw provider token leaked")))
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

    assert transaction_repo.update_status.await_args_list[1].args == ("tx-1", TransactionStatusEnum.FAILED.value)
    error_message = transaction_repo.update_status.await_args_list[1].kwargs["error_message"]
    assert error_message == "Data purchase could not be completed. Please try again."
    assert "raw provider token leaked" not in error_message


@pytest.mark.asyncio
async def test_data_executor_policy_block_does_not_call_provider(tmp_path: Path) -> None:
    _install_disabled_data_policy(tmp_path)
    try:
        provider = SimpleNamespace(
            purchase_data=AsyncMock(return_value={"success": True, "transaction_id": "provider-1"})
        )
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
