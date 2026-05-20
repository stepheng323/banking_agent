from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.receipt.src.notification_consumer import NotificationJobConsumer


@pytest.mark.asyncio
async def test_notification_consumer_delivers_payload_with_metadata_and_dedupe_key() -> None:
    delivery_service = SimpleNamespace(deliver_intents=AsyncMock(return_value=SimpleNamespace(status="delivered")))
    consumer = NotificationJobConsumer(delivery_service=delivery_service)

    await consumer.process_job(
        {
            "phone_number": "2348162511023",
            "channel": "whatsapp",
            "intents": [{"type": "say", "text": "Done"}],
            "metadata": {"source": "message_consumer", "message_id": "wamid-1"},
            "dedupe_key": "wamid-1",
            "strict_actionable": True,
        }
    )

    delivery_service.deliver_intents.assert_awaited_once_with(
        phone_number="2348162511023",
        channel="whatsapp",
        intents=[{"type": "say", "text": "Done"}],
        metadata={"source": "message_consumer", "message_id": "wamid-1"},
        dedupe_key="wamid-1",
        strict_actionable=True,
    )


@pytest.mark.asyncio
async def test_notification_consumer_supports_wrapped_payload() -> None:
    delivery_service = SimpleNamespace(deliver_intents=AsyncMock(return_value=SimpleNamespace(status="delivered")))
    consumer = NotificationJobConsumer(delivery_service=delivery_service)

    await consumer.process_job(
        {
            "payload": {
                "phone_number": "chat-1",
                "channel": "telegram",
                "intents": [{"type": "typing"}],
                "metadata": {"source": "progress"},
                "dedupe_key": "typing-1",
                "strict_actionable": False,
            }
        }
    )

    delivery_service.deliver_intents.assert_awaited_once_with(
        phone_number="chat-1",
        channel="telegram",
        intents=[{"type": "typing"}],
        metadata={"source": "progress"},
        dedupe_key="typing-1",
        strict_actionable=False,
    )


@pytest.mark.asyncio
async def test_notification_consumer_drops_invalid_payload_without_delivery() -> None:
    delivery_service = SimpleNamespace(deliver_intents=AsyncMock())
    consumer = NotificationJobConsumer(delivery_service=delivery_service)

    await consumer.process_job({"phone_number": "", "intents": [{"type": "say", "text": "Done"}]})
    await consumer.process_job({"phone_number": "2348162511023", "intents": []})

    delivery_service.deliver_intents.assert_not_awaited()
