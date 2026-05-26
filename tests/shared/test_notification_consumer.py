from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.receipt.src.notification_consumer import NotificationJobConsumer
from shared.messaging.prompt_suppression import (
    PENDING_INPUT_PROMPT_METADATA_KEY,
    PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY,
    PENDING_INPUT_PROMPT_THREAD_KEY,
    latest_inbound_delivery_target_key,
)


class _RedisStub:
    def __init__(self, latest_message_id: str | None) -> None:
        self.latest_message_id = latest_message_id
        self.get_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.latest_message_id


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
async def test_notification_consumer_rejects_wrapped_payload() -> None:
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

    delivery_service.deliver_intents.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_consumer_drops_invalid_payload_without_delivery() -> None:
    delivery_service = SimpleNamespace(deliver_intents=AsyncMock())
    consumer = NotificationJobConsumer(delivery_service=delivery_service)

    await consumer.process_job({"phone_number": "", "intents": [{"type": "say", "text": "Done"}]})
    await consumer.process_job({"phone_number": "2348162511023", "intents": []})

    delivery_service.deliver_intents.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_consumer_suppresses_stale_pending_input_prompt() -> None:
    delivery_service = SimpleNamespace(deliver_intents=AsyncMock())
    key = latest_inbound_delivery_target_key("telegram", "chat-1")
    redis = _RedisStub(latest_message_id="msg-2")
    consumer = NotificationJobConsumer(
        delivery_service=delivery_service,
        redis_client=redis,
        prompt_debounce_seconds=0,
    )

    await consumer.process_job(
        {
            "phone_number": "chat-1",
            "channel": "telegram",
            "intents": [{"type": "say", "text": "How much would you like to send?"}],
            "metadata": {
                "source": "message_consumer",
                "message_id": "msg-1",
                PENDING_INPUT_PROMPT_METADATA_KEY: True,
                PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY: "msg-1",
                PENDING_INPUT_PROMPT_THREAD_KEY: key,
            },
        }
    )

    assert redis.get_calls == [key]
    delivery_service.deliver_intents.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_consumer_delivers_current_pending_input_prompt() -> None:
    delivery_service = SimpleNamespace(deliver_intents=AsyncMock(return_value=SimpleNamespace(status="delivered")))
    key = latest_inbound_delivery_target_key("whatsapp", "2348162511023")
    redis = _RedisStub(latest_message_id="msg-1")
    consumer = NotificationJobConsumer(
        delivery_service=delivery_service,
        redis_client=redis,
        prompt_debounce_seconds=0,
    )

    await consumer.process_job(
        {
            "phone_number": "2348162511023",
            "channel": "whatsapp",
            "intents": [{"type": "say", "text": "How much would you like to send?"}],
            "metadata": {
                "source": "message_consumer",
                "message_id": "msg-1",
                PENDING_INPUT_PROMPT_METADATA_KEY: True,
                PENDING_INPUT_PROMPT_ORIGIN_MESSAGE_ID_KEY: "msg-1",
                PENDING_INPUT_PROMPT_THREAD_KEY: key,
            },
        }
    )

    assert redis.get_calls == [key]
    delivery_service.deliver_intents.assert_awaited_once()
