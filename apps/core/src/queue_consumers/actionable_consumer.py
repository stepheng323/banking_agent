"""Consumer for persisting actionable messages sent via outbox."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from shared.database.enums import ActionableMessageTypeEnum
from shared.database.models import ActionableMessage
from shared.queue.messages import ACTIONABLE_MESSAGES_QUEUE
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ActionableMessageConsumer:
    """Consumes actionable message jobs and persists them to the DB."""

    def __init__(self, redis_queue: RedisQueue):
        self.queue = redis_queue
        self.running = False

    async def process_job(self, payload: dict[str, Any]) -> None:
        """Process a single actionable message persistence job."""
        channel = payload.get("channel")
        message_id = payload.get("message_id")
        phone_number = payload.get("phone_number")
        actionable_payload = payload.get("payload")

        if not all([channel, message_id, phone_number, actionable_payload]):
            logger.warning("actionable_consumer_missing_fields", payload=payload)
            return

        try:
            async with UnitOfWork() as uow:
                if not uow.users or not uow.actionable_messages:
                    logger.error("missing_repositories")
                    return

                # Find the user by channel identity
                user = await uow.users.get_by_channel_identity(phone_number, channel)
                if not user:
                    logger.warning("actionable_consumer_user_not_found", phone_number=phone_number, channel=channel)
                    return

                # Assuming the actionable payload has the specific intent type or context we want
                # Right now, it's mostly Receipts -> mapping to generic 'receipt' or similar.
                # If we need a stricter enum matching, we might map dynamically. For now, default to 'general'.
                # Actually, our ActionableMessageTypeEnum has values like "receipt" or "invoice".
                # We can default to RECEIPT if transaction_id is present, which is the primary use case right now.
                msg_type = (
                    ActionableMessageTypeEnum.RECEIPT
                    if "transaction_id" in actionable_payload
                    else ActionableMessageTypeEnum.GENERIC
                )

                # Persist
                new_msg = ActionableMessage(
                    user_id=user.id,
                    channel_message_id=message_id,  # Using this column for both WA and Telegram message IDs
                    message_type=msg_type,
                    message_data=actionable_payload,
                    expires_at=datetime.utcnow() + timedelta(days=7),  # Valid for 7 days
                )

                # Use the underlying DB session directly for insertion since repository lacks a generic `create`
                uow.actionable_messages.db.add(new_msg)

                await uow.commit()
                logger.debug("actionable_message_persisted", user_id=user.id, message_id=message_id)

        except Exception as e:
            logger.error("actionable_consumer_failed", message_id=message_id, error=str(e), exc_info=True)

    async def start(self, queue_name: str = ACTIONABLE_MESSAGES_QUEUE):
        """Start consuming actionable messages queue."""
        self.running = True
        logger.info("actionable_consumer_started", queue=queue_name)

        await self.queue.connect()

        while self.running:
            try:
                job_data = await self.queue.dequeue_blocking(queue_name=queue_name, timeout=5)
                if job_data:
                    await self.process_job(job_data)

            except asyncio.CancelledError:
                logger.info("actionable_consumer_cancelled")
                break
            except Exception as e:
                logger.error("actionable_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)

        await self.queue.close()
        logger.info("actionable_consumer_stopped")

    def stop(self):
        """Stop the consumer."""
        self.running = False
