from typing import Any

import pytest

import shared.messaging.outbox as outbox_module
from shared.messaging.intents import Say, SendTyping
from shared.messaging.outbox import enqueue_outbox_intents, enqueue_outbox_say, enqueue_outbox_typing
from shared.queue.contracts import get_contract_by_topic, resolve_contract_from_redis_stream_name


class _PublisherStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.calls.append((topic, message))


def test_notification_send_contract_routes_to_receipt_worker_transports() -> None:
    contract = get_contract_by_topic("notification.send")

    assert contract.redis_stream_name == "async:notifications"
    assert resolve_contract_from_redis_stream_name("async:notifications") == contract


@pytest.mark.asyncio
async def test_enqueue_outbox_intents_publishes_notification_send_payload() -> None:
    publisher = _PublisherStub()

    result = await enqueue_outbox_intents(
        publisher,
        "2348162511023",
        "whatsapp",
        [Say(text="Confirm transfer", actionable_payload={"task_id": "task-1"})],
        metadata={"source": "message_consumer", "message_id": "wamid-1", "strict_actionable": True},
    )

    assert result.delivered is True
    assert publisher.calls == [
        (
            "notification.send",
            {
                "phone_number": "2348162511023",
                "channel": "whatsapp",
                "intents": [
                    {
                        "type": "say",
                        "text": "Confirm transfer",
                        "actionable_payload": {"task_id": "task-1"},
                    }
                ],
                "metadata": {"source": "message_consumer", "message_id": "wamid-1", "strict_actionable": True},
                "dedupe_key": "wamid-1",
                "strict_actionable": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_enqueue_outbox_helpers_publish_say_and_typing() -> None:
    publisher = _PublisherStub()

    await enqueue_outbox_say(publisher, "chat-1", "telegram", "Hello", metadata={"dedupe_key": "say-1"})
    await enqueue_outbox_typing(publisher, "chat-1", "telegram", metadata={"dedupe_key": "typing-1"})

    assert publisher.calls[0][0] == "notification.send"
    assert publisher.calls[0][1]["intents"] == [{"type": "say", "text": "Hello", "actionable_payload": None}]
    assert publisher.calls[0][1]["dedupe_key"] == "say-1"
    assert publisher.calls[1][0] == "notification.send"
    assert publisher.calls[1][1]["intents"] == [{"type": "typing", "actionable_payload": None}]
    assert publisher.calls[1][1]["dedupe_key"] == "typing-1"


@pytest.mark.asyncio
async def test_enqueue_outbox_prefers_explicit_dedupe_key_over_message_id() -> None:
    publisher = _PublisherStub()

    await enqueue_outbox_typing(
        publisher,
        "chat-1",
        "telegram",
        metadata={"message_id": "msg-1", "dedupe_key": "typing-heartbeat-2"},
    )

    assert publisher.calls[0][1]["dedupe_key"] == "typing-heartbeat-2"


@pytest.mark.asyncio
async def test_enqueue_outbox_requires_publisher_for_nonempty_intents() -> None:
    with pytest.raises(RuntimeError, match="outbox_publisher_required"):
        await enqueue_outbox_intents(None, "2348162511023", "whatsapp", [SendTyping()])


@pytest.mark.asyncio
async def test_enqueue_outbox_empty_intents_is_noop_without_delivery_service() -> None:
    result = await enqueue_outbox_intents(None, "2348162511023", "whatsapp", [])

    assert result.delivered is True
    assert "DeliveryService" not in vars(outbox_module)
