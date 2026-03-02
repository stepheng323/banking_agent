"""Outbox helpers for direct presenter-based messaging delivery."""

from typing import Any

from apps.core.src.agent.orchestrator.models.intents import Say, UiIntent
from shared.queue.adapter import QueuePublisher
from shared.services.delivery_service import DeliveryService

_delivery_service: DeliveryService | None = None


def _get_delivery_service() -> DeliveryService:
    global _delivery_service
    if _delivery_service is None:
        _delivery_service = DeliveryService()
    return _delivery_service


async def enqueue_outbox_intents(
    publisher: QueuePublisher | None,
    phone_number: str,
    channel: str,
    intents: list[UiIntent | dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Deliver intents directly.

    The publisher argument is intentionally retained for backwards
    compatibility with existing call sites.
    """
    del publisher

    if not intents:
        return

    dedupe_key = None
    if metadata:
        dedupe_key = metadata.get("message_id") or metadata.get("idempotency_key") or metadata.get("dedupe_key")

    await _get_delivery_service().deliver_intents(
        phone_number=phone_number,
        channel=channel,
        intents=intents,
        metadata=metadata or {},
        dedupe_key=str(dedupe_key) if dedupe_key else None,
    )


async def enqueue_outbox_say(
    publisher: QueuePublisher | None,
    phone_number: str,
    channel: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Deliver one text message directly."""
    if not text:
        return
    await enqueue_outbox_intents(publisher, phone_number, channel, [Say(text=text)], metadata=metadata)
