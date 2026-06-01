from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.transactions.runtime import bill_completion_notifications as notifications
from banking.transactions.runtime.async_completion import record_group_leg_and_maybe_build_summary
from banking.transactions.runtime.bill_completion_notifications import (
    BillCompletionNotifier,
    build_airtime_completion_context,
    build_data_completion_context,
    merge_completion_context,
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

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


class _SuggestionServiceStub:
    def __init__(self, message: str | None = "Would you like to save this MTN line?") -> None:
        self.check_and_suggest_beneficiary = AsyncMock(return_value=message)


def _delivery() -> SimpleNamespace:
    return SimpleNamespace(deliver_text=AsyncMock())


def _airtime_context(*, async_group: dict | None = None, scheduled_meta: dict | None = None) -> dict:
    message = {
        "transaction_id": "tx-1",
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_identity": "927331985",
        "language": "en",
    }
    if async_group:
        message["async_group"] = async_group
    if scheduled_meta:
        message["scheduled_meta"] = scheduled_meta
    return build_airtime_completion_context(
        message=message,
        airtime_data={
            "amount": Decimal("2000.00"),
            "phone_number": "08031234567",
            "network": "MTN",
            "recipient_name": "Tolu",
            "source_account_id": "acc-1",
            "source_account_number": "0000000001",
            "source_bank_name": "Access Bank",
        },
        amount=Decimal("2000.00"),
    )


def _data_context() -> dict:
    return build_data_completion_context(
        message={
            "transaction_id": "tx-data",
            "phone_number": "2348162511023",
            "channel": "telegram",
            "channel_identity": "927331985",
            "language": "en",
        },
        data_purchase={
            "amount": Decimal("1500.00"),
            "target_phone": "08031234567",
            "network": "MTN",
            "recipient_name": "Tolu",
            "plan_code": "mtn-1gb",
            "plan_name": "1GB Weekly",
            "source_account_id": "acc-1",
        },
        amount=Decimal("1500.00"),
    )


def _transaction(transaction_type: str, context: dict, **overrides) -> SimpleNamespace:
    base = {
        "id": "tx-1" if transaction_type == "airtime" else "tx-data",
        "transaction_type": transaction_type,
        "amount": Decimal("2000.00") if transaction_type == "airtime" else Decimal("1500.00"),
        "transaction_id": "provider-ref-1",
        "target_phone_number": "08031234567",
        "mobile_network": "MTN",
        "biller_item_name": "1GB Weekly" if transaction_type == "data" else None,
        "source_account_id": "acc-1",
        "source_account_number": "0000000001",
        "source_bank_name": "Access Bank",
        "error_message": None,
        "service_metadata": merge_completion_context({}, context),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_airtime_success_sends_legacy_success_message_and_suggestion() -> None:
    delivery = _delivery()
    suggestion_service = _SuggestionServiceStub()
    notifier = BillCompletionNotifier(
        delivery_service=delivery,
        beneficiary_suggestion_service=suggestion_service,
    )
    tx = _transaction("airtime", _airtime_context())

    await notifier.notify(tx, "successful")

    delivery.deliver_text.assert_awaited_once()
    kwargs = delivery.deliver_text.await_args.kwargs
    assert kwargs["phone_number"] == "927331985"
    assert kwargs["channel"] == "telegram"
    assert kwargs["dedupe_key"] == "airtime:success:tx-1"
    assert "Airtime purchase successful" in kwargs["text"]
    assert "Recipient: Tolu (08031234567)" in kwargs["text"]
    assert "Would you like to save this MTN line?" in kwargs["text"]


@pytest.mark.asyncio
async def test_data_success_sends_legacy_success_message_and_suggestion() -> None:
    delivery = _delivery()
    suggestion_service = _SuggestionServiceStub()
    notifier = BillCompletionNotifier(
        delivery_service=delivery,
        beneficiary_suggestion_service=suggestion_service,
    )
    tx = _transaction("data", _data_context())

    await notifier.notify(tx, "successful")

    kwargs = delivery.deliver_text.await_args.kwargs
    assert kwargs["dedupe_key"] == "data:success:tx-data"
    assert "Data purchase successful" in kwargs["text"]
    assert "Plan: 1GB Weekly" in kwargs["text"]
    assert "Recipient: Tolu (MTN)" in kwargs["text"]
    assert "Would you like to save this MTN line?" in kwargs["text"]


@pytest.mark.asyncio
async def test_bill_failure_after_debit_sends_refund_pending_wording() -> None:
    delivery = _delivery()
    notifier = BillCompletionNotifier(delivery_service=delivery)
    tx = _transaction("airtime", _airtime_context(), error_message="Provider failed")

    await notifier.notify(tx, "failed_refund_pending")

    kwargs = delivery.deliver_text.await_args.kwargs
    assert kwargs["dedupe_key"] == "airtime:failed-refund-pending:tx-1"
    assert "could not be completed after your account was debited" in kwargs["text"]
    assert "refund has been started" in kwargs["text"]


@pytest.mark.asyncio
async def test_refund_confirmed_sends_reversal_wording_and_marks_scheduled_failed(monkeypatch) -> None:
    delivery = _delivery()
    update_scheduled_run = AsyncMock()
    monkeypatch.setattr(notifications.scheduled_runs, "update_scheduled_run", update_scheduled_run)
    context = _airtime_context(
        scheduled_meta={
            "schedule_id": "schedule-1",
            "schedule_run_id": "run-1",
            "run_source": "scheduled",
        }
    )
    notifier = BillCompletionNotifier(delivery_service=delivery)
    tx = _transaction("airtime", context)

    await notifier.notify(tx, "refunded")

    update_scheduled_run.assert_awaited_once()
    assert update_scheduled_run.await_args.kwargs["status"] == "failed"
    assert update_scheduled_run.await_args.kwargs["transaction_id"] == "tx-1"
    assert "has been refunded to your bank account" in delivery.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_missing_completion_context_does_not_crash_or_deliver() -> None:
    delivery = _delivery()
    notifier = BillCompletionNotifier(delivery_service=delivery)
    tx = _transaction("airtime", {}, service_metadata={})

    await notifier.notify(tx, "successful")

    delivery.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_grouped_async_success_emits_batch_summary_from_reconstructed_context() -> None:
    delivery = _delivery()
    redis_client = _RedisStub()
    group_meta = {
        "async_group_id": "group-async-bill",
        "async_group_size": 2,
        "async_group_kind": "mixed_batch",
        "async_group_index": 2,
    }
    first_leg = {
        "transaction_id": "tx-transfer",
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_identity": "927331985",
        "language": "en",
        "async_group": {**group_meta, "async_group_index": 1},
    }
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=first_leg,
        task_type="transfer",
        payload={
            "amount": Decimal("5000.00"),
            "recipient_name": "Tolu",
            "recipient_resolved_name": "Tolulope Johnson",
            "recipient_bank_name": "First Bank",
            "recipient_account": "2010000003",
            "final_status": "success",
        },
        locale="en",
    )
    notifier = BillCompletionNotifier(delivery_service=delivery, redis_client=redis_client)
    tx = _transaction("airtime", _airtime_context(async_group=group_meta))

    await notifier.notify(tx, "successful")

    delivery.deliver_text.assert_awaited_once()
    kwargs = delivery.deliver_text.await_args.kwargs
    assert kwargs["dedupe_key"] == "airtime:batch:final:tx-1"
    assert "Transaction Summary" in kwargs["text"]
    assert "Airtime" in kwargs["text"]
    assert "Tolulope Johnson" in kwargs["text"]
