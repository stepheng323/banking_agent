from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from apps.receipt.src.consumer import ReceiptJobConsumer
from shared.queue.messages import OUTBOX_QUEUE


@pytest.mark.asyncio
async def test_receipt_consumer_processes_top_level_payload() -> None:
    consumer = ReceiptJobConsumer()

    render_receipt = AsyncMock(return_value=b"png-bytes")
    enqueue = AsyncMock()
    rpush = AsyncMock()
    expire = AsyncMock()

    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt, close=AsyncMock()))
    consumer.queue = cast(Any, SimpleNamespace(enqueue=enqueue, _redis=SimpleNamespace(rpush=rpush, expire=expire)))

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
    enqueue.assert_awaited_once()
    assert enqueue.await_args is not None
    kwargs = enqueue.await_args.kwargs
    assert kwargs["queue_name"] == OUTBOX_QUEUE
    assert kwargs["message"]["phone_number"] == "2348000000001"
    assert kwargs["message"]["intents"][0]["type"] == "show_receipt"
    rpush.assert_awaited_once_with("receipt:signal:test-1", "DONE")
    expire.assert_awaited_once_with("receipt:signal:test-1", 60)


@pytest.mark.asyncio
async def test_receipt_consumer_processes_wrapped_payload() -> None:
    consumer = ReceiptJobConsumer()

    render_receipt = AsyncMock(return_value=b"png-bytes")
    enqueue = AsyncMock()
    rpush = AsyncMock()
    expire = AsyncMock()

    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt, close=AsyncMock()))
    consumer.queue = cast(Any, SimpleNamespace(enqueue=enqueue, _redis=SimpleNamespace(rpush=rpush, expire=expire)))

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
    enqueue.assert_awaited_once()
    assert enqueue.await_args is not None
    kwargs = enqueue.await_args.kwargs
    assert kwargs["message"]["phone_number"] == "2348000000002"
    assert kwargs["message"]["intents"][0]["type"] == "show_receipt"
    rpush.assert_awaited_once_with("receipt:signal:test-2", "DONE")
    expire.assert_awaited_once_with("receipt:signal:test-2", 60)


@pytest.mark.asyncio
async def test_receipt_consumer_appends_beneficiary_suggestion_after_receipt() -> None:
    consumer = ReceiptJobConsumer()

    render_receipt = AsyncMock(return_value=b"png-bytes")
    enqueue = AsyncMock()
    rpush = AsyncMock()
    expire = AsyncMock()

    consumer.renderer = cast(Any, SimpleNamespace(render_receipt=render_receipt, close=AsyncMock()))
    consumer.queue = cast(Any, SimpleNamespace(enqueue=enqueue, _redis=SimpleNamespace(rpush=rpush, expire=expire)))

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

    assert enqueue.await_args is not None
    kwargs = enqueue.await_args.kwargs
    intents = kwargs["message"]["intents"]
    assert intents[0]["type"] == "show_receipt"
    assert intents[1] == {"type": "say", "text": "Would you like to save Mercy Johnson?"}
