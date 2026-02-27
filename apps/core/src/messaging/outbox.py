"""Outbox helpers for presenter-based messaging."""

from typing import Any

from apps.core.src.agent.orchestrator.models.intents import Say, UiIntent
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def enqueue_outbox_intents(
    publisher: QueuePublisher | None,
    phone_number: str,
    channel: str,
    intents: list[UiIntent | dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Enqueue intents for presenter-based delivery."""
    if not publisher:
        logger.error("outbox_publisher_missing", phone=phone_number, channel=channel)
        return

    if not intents:
        return

    serialized: list[dict[str, Any]] = []
    for intent in intents:
        if isinstance(intent, dict):
            serialized.append(intent)
        else:
            serialized.append(intent.to_dict())

    await publisher.publish(
        topic="notification.send",
        message={
            "phone_number": phone_number,
            "channel": channel,
            "intents": serialized,
            "metadata": metadata or {},
        },
    )


async def enqueue_outbox_say(
    publisher: QueuePublisher | None,
    phone_number: str,
    channel: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Enqueue a single text intent."""
    if not text:
        return
    await enqueue_outbox_intents(publisher, phone_number, channel, [Say(text=text)], metadata=metadata)
