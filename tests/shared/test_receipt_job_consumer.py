from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from apps.receipt.src.consumer import ReceiptJobConsumer


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
            "source": {"name": "First Bank", "account_name": "Gaines"},
            "narration": "offering",
        },
        "transaction_reference": "TRX-001",
        "signal_key": "receipt:signal:test-1",
    }

    await consumer._process_job(job)

    render_receipt.assert_awaited_once_with(
        transfer_data=job["transfer_data"],
        transaction_reference="TRX-001",
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
async def test_receipt_consumer_processes_wrapped_payload() -> None:
    delivery_service = cast(Any, SimpleNamespace(deliver_intents=AsyncMock(), deliver_text=AsyncMock()))
    redis_client = cast(Any, SimpleNamespace(rpush=AsyncMock(), expire=AsyncMock()))
    consumer = ReceiptJobConsumer(delivery_service=delivery_service, redis_client=redis_client)

    render_receipt = AsyncMock(return_value=b"png-bytes")
    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt, close=AsyncMock()))
    rpush = redis_client.rpush
    expire = redis_client.expire

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

    render_receipt.assert_awaited_once_with(
        transfer_data=job["payload"]["transfer_data"],
        transaction_reference="TRX-002",
    )
    delivery_service.deliver_intents.assert_awaited_once()
    assert delivery_service.deliver_intents.await_args is not None
    kwargs = delivery_service.deliver_intents.await_args.kwargs
    assert kwargs["phone_number"] == "2348000000002"
    assert kwargs["intents"][0]["type"] == "show_receipt"
    rpush.assert_awaited_once_with("receipt:signal:test-2", "DONE")
    expire.assert_awaited_once_with("receipt:signal:test-2", 60)


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
