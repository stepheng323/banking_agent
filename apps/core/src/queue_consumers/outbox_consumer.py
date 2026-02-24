"""Outbox Consumer for processing outgoing messages from the queue.

This consumer handles the 'Speaking' part of the system, leveraging Presenters
to send messages to various channels (WhatsApp, etc.).
"""

import asyncio
from typing import Any

from apps.core.src.agent.orchestrator.models.intents import UiIntent, reconstruct_intent
from apps.core.src.messaging.presenters.base import PresentationContext
from apps.core.src.messaging.presenters.factory import PresenterFactory
from shared.clients.abstractions.messaging import MessagingClient
from shared.queue.messages import ACTIONABLE_MESSAGES_QUEUE, OUTBOX_QUEUE
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OutboxConsumer:
    """Consumes outbox jobs (UI Intents) and sends them to the appropriate channel."""

    def __init__(
        self,
        redis_queue: RedisQueue,
        messaging_clients: dict[str, MessagingClient],
        default_channel: str = "whatsapp",
    ):
        self.queue = redis_queue
        self.messaging_clients = messaging_clients
        self.default_channel = default_channel
        self.running = False

    def _get_client(self, channel: str) -> MessagingClient:
        """Select the messaging client for a given channel."""
        client = self.messaging_clients.get(channel)
        if client:
            return client
        default_client = self.messaging_clients.get(self.default_channel)
        if default_client:
            logger.warning("outbox_channel_fallback", requested=channel, using=self.default_channel)
            return default_client
        first_client = next(iter(self.messaging_clients.values()), None)
        if not first_client:
            raise RuntimeError("No messaging clients configured")
        return first_client

    async def process_job(self, payload: dict[str, Any]) -> None:
        """Process a single outbox job."""
        phone_number = payload.get("phone_number")
        channel = payload.get("channel", self.default_channel)
        intents_data = payload.get("intents", [])

        if not phone_number:
            logger.warning("outbox_missing_phone_number", payload=payload)
            return

        try:
            intents: list[UiIntent] = []
            for item in intents_data:
                intent = reconstruct_intent(item)
                if intent:
                    intents.append(intent)
                else:
                    logger.warning("outbox_unknown_intent", item=item)

            if not intents:
                logger.warning("outbox_no_valid_intents", payload=payload)
                return

            client = self._get_client(channel)
            presenter = PresenterFactory.create(channel=channel, client=client)

            supports_flows = getattr(client, "supports_flows", True)
            context = PresentationContext(
                channel=channel,
                phone_number=phone_number,
                capabilities={"flows": supports_flows},
                metadata=payload.get("metadata", {}),
            )

            result = await presenter.present(intents, context)
            logger.info("outbox_sent", phone=phone_number, count=len(intents), channel=channel, success=result.success)

            actionable_payload = next((i.actionable_payload for i in intents if i.actionable_payload), None)

            if result.success and result.message_ids and actionable_payload:
                for msg_id in result.message_ids:
                    await self.queue.enqueue(
                        queue_name=ACTIONABLE_MESSAGES_QUEUE,
                        message={
                            "channel": channel,
                            "message_id": msg_id,
                            "phone_number": phone_number,
                            "payload": actionable_payload,
                        },
                    )
                    logger.info("enqueued_actionable_message", msg_id=msg_id)

        except Exception as e:
            logger.error("outbox_processing_failed", phone=phone_number, error=str(e), exc_info=True)

    async def start(self, queue_name: str = OUTBOX_QUEUE):
        """Start consuming outbox queue."""
        self.running = True
        logger.info("outbox_consumer_started", queue=queue_name)

        await self.queue.connect()

        while self.running:
            try:
                job_data = await self.queue.dequeue_blocking(queue_name=queue_name, timeout=5)
                if job_data:
                    await self.process_job(job_data)

            except asyncio.CancelledError:
                logger.info("outbox_consumer_cancelled")
                break
            except Exception as e:
                logger.error("outbox_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)

        await self.queue.close()
        logger.info("outbox_consumer_stopped")

    def stop(self):
        """Stop the consumer."""
        self.running = False
