from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from apps.receipt.src.consumer import ReceiptJobConsumer
from shared.config.settings import settings


@pytest.mark.asyncio
async def test_receipt_consumer_processes_top_level_payload() -> None:
    delivery_service = cast(Any, SimpleNamespace(deliver_intents=AsyncMock(), deliver_text=AsyncMock()))
    redis_client = cast(Any, SimpleNamespace(rpush=AsyncMock(), expire=AsyncMock()))
    consumer = ReceiptJobConsumer(delivery_service=delivery_service, redis_client=redis_client)

    render_receipt = AsyncMock(return_value=b"png-bytes")
    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt, close=AsyncMock()))
    rpush = redis_client.rpush
    expire = redis_client.expire

    job: dict[str, Any] = {
        "phone_number": "2348000000001",
        "transfer_data": {
            "amount": 5000,
            "recipient": {"name": "Pastor Bright", "account_number": "0760505261", "bank_name": "Access Bank"},
            "source": {"name": "First Bank", "account_name": "Gaines", "account_number": "0123456789"},
            "narration": "offering",
            "channel": "whatsapp",
            "session_id": "session-123",
            "processor_name": f"{settings.app_name} Gateway",
        },
        "transaction_reference": "TRX-001",
        "signal_key": "receipt:signal:test-1",
    }

    await consumer._process_job(job)

    render_receipt.assert_awaited_once_with(
        transfer_data=job["transfer_data"],
        transaction_reference="TRX-001",
        attempt=1,
    )
    delivery_service.deliver_intents.assert_awaited_once()
    assert delivery_service.deliver_intents.await_args is not None
    kwargs = delivery_service.deliver_intents.await_args.kwargs
    assert kwargs["phone_number"] == "2348000000001"
    assert kwargs["channel"] == "whatsapp"
    assert kwargs["intents"][0]["type"] == "show_receipt"
    assert kwargs["dedupe_key"] == "receipt:TRX-001"
    assert kwargs["strict_actionable"] is True
    rpush.assert_awaited_once_with("receipt:signal:test-1", "DONE")
    expire.assert_awaited_once_with("receipt:signal:test-1", 60)


@pytest.mark.asyncio
async def test_receipt_consumer_rejects_wrapped_payload() -> None:
    delivery_service = cast(Any, SimpleNamespace(deliver_intents=AsyncMock(), deliver_text=AsyncMock()))
    redis_client = cast(Any, SimpleNamespace(rpush=AsyncMock(), expire=AsyncMock()))
    consumer = ReceiptJobConsumer(delivery_service=delivery_service, redis_client=redis_client)

    render_receipt = AsyncMock(return_value=b"png-bytes")
    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt, close=AsyncMock()))
    job: dict[str, Any] = {
        "payload": {
            "phone_number": "2348000000002",
            "transfer_data": {
                "amount": 7500,
                "recipient": {"name": "Mum", "account_number": "1234567890", "bank_name": "GTBank"},
                "source": {"account_name": "User Account"},
                "narration": "family support",
            },
            "transaction_reference": "TRX-002",
        },
        "signal_key": "receipt:signal:test-2",
    }

    await consumer._process_job(job)

    render_receipt.assert_not_awaited()
    delivery_service.deliver_intents.assert_not_awaited()
    redis_client.rpush.assert_not_awaited()
    redis_client.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_consumer_appends_beneficiary_suggestion_after_receipt() -> None:
    delivery_service = cast(Any, SimpleNamespace(deliver_intents=AsyncMock(), deliver_text=AsyncMock()))
    redis_client = cast(Any, SimpleNamespace(rpush=AsyncMock(), expire=AsyncMock()))
    consumer = ReceiptJobConsumer(delivery_service=delivery_service, redis_client=redis_client)

    render_receipt = AsyncMock(return_value=b"png-bytes")
    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt, close=AsyncMock()))

    job: dict[str, Any] = {
        "phone_number": "2348000000003",
        "transfer_data": {
            "amount": 5000,
            "recipient": {"name": "Mercy Johnson", "account_number": "8162511023", "bank_name": "Opay"},
            "source": {"account_name": "Gaines"},
            "narration": "",
        },
        "transaction_reference": "TRX-003",
        "signal_key": "receipt:signal:test-3",
        "beneficiary_suggestion_message": "Would you like to save Mercy Johnson?",
    }

    await consumer._process_job(job)

    assert delivery_service.deliver_intents.await_args is not None
    kwargs = delivery_service.deliver_intents.await_args.kwargs
    intents = kwargs["intents"]
    assert intents[0]["type"] == "show_receipt"
    assert intents[1] == {"type": "say", "text": "Would you like to save Mercy Johnson?"}


@pytest.mark.asyncio
async def test_receipt_consumer_sends_generation_notice_before_rendering_when_requested() -> None:
    events: list[str] = []

    async def deliver_text(**kwargs: Any) -> None:
        events.append("generation_notice")
        assert kwargs["text"] == "I'm generating your receipt now. I'll send it to you as an image shortly."
        assert kwargs["dedupe_key"] == "receipt-generating:TRX-004"

    async def render_receipt(**kwargs: Any) -> bytes:
        del kwargs
        events.append("render")
        return b"png-bytes"

    async def deliver_intents(**kwargs: Any) -> None:
        del kwargs
        events.append("receipt")

    delivery_service = cast(
        Any,
        SimpleNamespace(
            deliver_intents=AsyncMock(side_effect=deliver_intents),
            deliver_text=AsyncMock(side_effect=deliver_text),
        ),
    )
    consumer = ReceiptJobConsumer(delivery_service=delivery_service, redis_client=None)
    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=AsyncMock(side_effect=render_receipt)))

    job: dict[str, Any] = {
        "phone_number": "2348000000004",
        "channel": "telegram",
        "channel_identity": "927331985",
        "language": "en",
        "send_generation_notice": True,
        "transfer_data": {
            "amount": 5000,
            "recipient": {"name": "Mercy Johnson", "account_number": "8162511023", "bank_name": "Opay"},
            "source": {"account_name": "Gaines"},
        },
        "transaction_reference": "TRX-004",
    }

    await consumer._process_job(job)

    assert events == ["generation_notice", "render", "receipt"]
    delivery_service.deliver_text.assert_awaited_once()
    delivery_service.deliver_intents.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_consumer_browser_closed_failure_retries_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    delivery_service = cast(Any, SimpleNamespace(deliver_intents=AsyncMock(), deliver_text=AsyncMock()))
    consumer = ReceiptJobConsumer(delivery_service=delivery_service, redis_client=None)

    render_receipt = AsyncMock(
        side_effect=[RuntimeError("Target page, context or browser has been closed"), b"png-bytes"],
    )
    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt))

    sleep_mock = AsyncMock()
    monkeypatch.setattr("apps.receipt.src.consumer.asyncio.sleep", sleep_mock)
    monkeypatch.setattr("apps.receipt.src.consumer.random.uniform", lambda *_: 0.0)

    job: dict[str, Any] = {
        "phone_number": "2348000000004",
        "transfer_data": {
            "amount": 5000,
            "recipient": {"name": "Mercy Johnson", "account_number": "8162511023", "bank_name": "Opay"},
            "source": {"account_name": "Gaines"},
        },
        "transaction_reference": "TRX-004",
    }

    await consumer._process_job(job)

    assert render_receipt.await_count == 2
    first_call = render_receipt.await_args_list[0].kwargs
    second_call = render_receipt.await_args_list[1].kwargs
    assert first_call["attempt"] == 1
    assert second_call["attempt"] == 2
    sleep_mock.assert_not_awaited()
    delivery_service.deliver_intents.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_consumer_non_browser_failure_uses_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    delivery_service = cast(Any, SimpleNamespace(deliver_intents=AsyncMock(), deliver_text=AsyncMock()))
    consumer = ReceiptJobConsumer(delivery_service=delivery_service, redis_client=None)

    render_receipt = AsyncMock(side_effect=[RuntimeError("temporary timeout"), b"png-bytes"])
    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt))

    sleep_mock = AsyncMock()
    monkeypatch.setattr("apps.receipt.src.consumer.asyncio.sleep", sleep_mock)
    monkeypatch.setattr("apps.receipt.src.consumer.random.uniform", lambda *_: 0.0)

    job: dict[str, Any] = {
        "phone_number": "2348000000005",
        "transfer_data": {
            "amount": 5000,
            "recipient": {"name": "Mercy Johnson", "account_number": "8162511023", "bank_name": "Opay"},
            "source": {"account_name": "Gaines"},
        },
        "transaction_reference": "TRX-005",
    }

    await consumer._process_job(job)

    sleep_mock.assert_awaited_once_with(1.0)
    delivery_service.deliver_intents.assert_awaited_once()
