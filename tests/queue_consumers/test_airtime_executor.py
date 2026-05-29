from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared.database.enums import TransactionStatusEnum
from banking.transactions.runtime.async_completion import record_group_leg_and_maybe_build_summary
from banking.transactions.runtime.executors.airtime import AirtimeExecutor


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
        "airtime_data": {
            "amount": 2000,
            "phone_number": "08031234567",
            "network": "MTN",
            "recipient_name": "Tolu",
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
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_transaction_id"] == "ref-1"
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_response"] == {
        "success": True,
        "reference": "ref-1",
    }
    provider.purchase_airtime.assert_awaited_once_with(
        amount=2000,
        recipient_phone="08031234567",
        network="MTN",
        reference="idem-1",
    )
    assert delivery_service.deliver_text.await_args.kwargs["phone_number"] == "927331985"
    assert delivery_service.deliver_text.await_args.kwargs["channel"] == "telegram"
    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Recipient: Tolu (08031234567)" in text
    assert "MTN" in text


@pytest.mark.asyncio
async def test_airtime_executor_success_appends_beneficiary_suggestion() -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(return_value={"success": True, "reference": "ref-1"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_airtime(_payload())

    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Tolu (08031234567)" in text
    assert "Would you like to save this MTN line?" in text
    suggestion_service.check_and_suggest_beneficiary.assert_awaited_once()
    assert suggestion_service.check_and_suggest_beneficiary.await_args.kwargs["recipient_data"]["name"] == "Tolu"


@pytest.mark.asyncio
async def test_airtime_executor_self_success_does_not_suggest_beneficiary() -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(return_value={"success": True, "reference": "ref-1"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    payload = _payload()
    payload["airtime_data"]["phone_number"] = "08162511023"
    payload["airtime_data"]["recipient_name"] = ""
    payload["airtime_data"]["is_self"] = True
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_airtime(payload)

    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Would you like to save" not in text
    suggestion_service.check_and_suggest_beneficiary.assert_not_awaited()


@pytest.mark.asyncio
async def test_airtime_executor_grouped_success_does_not_suggest_beneficiary() -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(return_value={"success": True, "reference": "ref-1"}))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
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
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_airtime(payload)

    suggestion_service.check_and_suggest_beneficiary.assert_not_awaited()


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
    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Provider down" in text
    assert "Tolu (08031234567)" in text


@pytest.mark.asyncio
async def test_airtime_executor_failure_uses_provider_error_field() -> None:
    provider = SimpleNamespace(
        purchase_airtime=AsyncMock(return_value={"success": False, "error": "Unsupported network"})
    )
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

    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.FAILED.value,
    )
    assert transaction_repo.update_status.await_args_list[1].kwargs["error_message"] == "Unsupported network"
    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Unsupported network" in text
    assert "Tolu (08031234567)" in text


@pytest.mark.asyncio
async def test_airtime_executor_provider_pending_stays_processing_without_duplicate_delivery() -> None:
    provider = SimpleNamespace(
        purchase_airtime=AsyncMock(return_value={"success": False, "message": "Bill payment is Pending"})
    )
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
        TransactionStatusEnum.PROCESSING.value,
    )
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_transaction_id"] == "idem-1"
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_response"] == {
        "success": False,
        "message": "Bill payment is Pending",
    }
    assert "error_message" not in transaction_repo.update_status.await_args_list[1].kwargs
    delivery_service.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_airtime_executor_provider_pending_appends_beneficiary_suggestion() -> None:
    provider = SimpleNamespace(
        purchase_airtime=AsyncMock(return_value={"success": False, "message": "Bill payment is Pending"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_airtime(_payload())

    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert text == "This airtime purchase is still processing.\n\nWould you like to save this MTN line?"


@pytest.mark.asyncio
async def test_scheduled_airtime_executor_provider_pending_sends_processing_message() -> None:
    provider = SimpleNamespace(
        purchase_airtime=AsyncMock(return_value={"success": False, "message": "Bill payment is Pending"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    payload = _payload()
    payload["scheduled_meta"] = {
        "schedule_id": "schedule-1",
        "schedule_run_id": "schedule-run-1",
        "run_source": "scheduled",
    }
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_airtime(payload)

    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "being processed" in text
    assert "Tolu (08031234567)" in text
    assert "failed" not in text.lower()


@pytest.mark.asyncio
async def test_airtime_executor_exception_uses_safe_user_error() -> None:
    provider = SimpleNamespace(purchase_airtime=AsyncMock(side_effect=RuntimeError("dsn password leaked")))
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

    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.FAILED.value,
    )
    error_message = transaction_repo.update_status.await_args_list[1].kwargs["error_message"]
    assert error_message == "Airtime purchase could not be completed. Please try again."
    assert "dsn password leaked" not in error_message


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


@pytest.mark.asyncio
async def test_airtime_executor_grouped_processing_emits_batch_summary_not_individual_message() -> None:
    provider = SimpleNamespace(
        purchase_airtime=AsyncMock(return_value={"success": False, "message": "Bill payment is Pending"})
    )
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    redis_client = _RedisStub()
    first_leg = _payload()
    first_leg["transaction_id"] = "tx-transfer"
    first_leg["async_group"] = {
        "async_group_id": "group-mixed-processing-airtime",
        "async_group_size": 2,
        "async_group_kind": "mixed_batch",
        "async_group_index": 1,
    }
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=first_leg,
        task_type="transfer",
        payload={
            "amount": 5000,
            "recipient_name": "Tolu",
            "recipient_resolved_name": "Tolulope Johnson",
            "recipient_bank_name": "First Bank",
            "recipient_account": "2010000003",
            "source_account_id": "acc-1",
            "source_account_number": "0000000003",
            "source_bank_name": "Access Bank",
            "source_affinity_mode": "explicit",
            "final_status": "success",
        },
        locale="en",
    )
    payload = _payload()
    payload["transaction_id"] = "tx-airtime"
    payload["async_group"] = {
        "async_group_id": "group-mixed-processing-airtime",
        "async_group_size": 2,
        "async_group_kind": "mixed_batch",
        "async_group_index": 2,
    }
    executor = AirtimeExecutor(
        bill_provider=provider,
        transaction_repo=transaction_repo,
        publisher=SimpleNamespace(),
        delivery_service=delivery_service,
        redis_client=redis_client,
    )

    await executor.handle_airtime(payload)

    delivery_service.deliver_text.assert_awaited_once()
    text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Transaction Update" in text
    assert "✓ ₦5,000 → Tolu (Tolulope Johnson)" in text
    assert "… *Airtime:* ₦2,000 for 08031234567 (MTN)" in text
    assert "awaiting provider confirmation" in text
    assert "Your airtime purchase" not in text
    assert "failed" not in text.lower()
