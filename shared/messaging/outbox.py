"""Outbox helpers for durable presenter-based messaging delivery."""

from typing import Any

from shared.messaging.intents import Say, SendTyping, UiIntent
from shared.queue.adapter import QueuePublisher
from banking.messaging.delivery.models import DeliveryAttemptResult


def _intent_to_dict(intent: UiIntent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(intent, dict):
        return dict(intent)
    to_dict = getattr(intent, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return converted
    model_dump = getattr(intent, "model_dump", None)
    if callable(model_dump):
        converted = model_dump()
        if isinstance(converted, dict):
            return converted
    raise TypeError(f"Unsupported outbox intent type: {type(intent).__name__}")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


async def enqueue_outbox_intents(
    publisher: QueuePublisher | None,
    phone_number: str,
    channel: str,
    intents: list[UiIntent | dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> DeliveryAttemptResult:
    """Publish UI intents to the durable notification delivery queue."""
    if not intents:
        return DeliveryAttemptResult(status="delivered")
    if publisher is None:
        raise RuntimeError("outbox_publisher_required")

    delivery_metadata = dict(metadata or {})
    dedupe_key_raw = (
        delivery_metadata.get("message_id")
        or delivery_metadata.get("idempotency_key")
        or delivery_metadata.get("dedupe_key")
    )
    strict_actionable = _parse_bool(delivery_metadata.get("strict_actionable"))

    await publisher.publish(
        topic="notification.send",
        message={
            "phone_number": phone_number,
            "channel": channel,
            "intents": [_intent_to_dict(intent) for intent in intents],
            "metadata": delivery_metadata,
            "dedupe_key": str(dedupe_key_raw) if dedupe_key_raw else None,
            "strict_actionable": strict_actionable,
        },
    )
    return DeliveryAttemptResult(status="delivered")


async def enqueue_outbox_say(
    publisher: QueuePublisher | None,
    phone_number: str,
    channel: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> DeliveryAttemptResult:
    """Publish one text message to the durable notification delivery queue."""
    if not text:
        return DeliveryAttemptResult(status="delivered")
    return await enqueue_outbox_intents(publisher, phone_number, channel, [Say(text=text)], metadata=metadata)


async def enqueue_outbox_typing(
    publisher: QueuePublisher | None,
    phone_number: str,
    channel: str,
    metadata: dict[str, Any] | None = None,
) -> DeliveryAttemptResult:
    """Publish a pure typing indicator to the durable notification delivery queue."""
    return await enqueue_outbox_intents(publisher, phone_number, channel, [SendTyping()], metadata=metadata)
