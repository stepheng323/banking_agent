"""Consumer for persisting actionable messages sent via outbox."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from shared.database.enums import ActionableMessageTypeEnum
from shared.database.models import ActionableMessage
from shared.queue.messages import ACTIONABLE_MESSAGES_QUEUE
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _is_duplicate_channel_message_error(error: IntegrityError) -> bool:
    """Return True when integrity error corresponds to duplicate channel message id."""
    message = str(getattr(error, "orig", error)).lower()
    return (
        ("duplicate key value" in message or "unique constraint" in message)
        and ("channel_message_id" in message or "wa_message_id" in message)
    )


class ActionableMessageConsumer:
    """Consumes actionable message jobs and persists them to the DB."""

    def __init__(self, redis_queue: RedisQueue):
        self.queue = redis_queue
        self.running = False

    async def _resolve_user(self, uow: UnitOfWork, channel: str, identity_or_phone: str) -> Any | None:
        """Resolve user strictly by channel identity."""
        return await uow.users.get_by_channel_identity(channel, identity_or_phone)

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
                user = await self._resolve_user(uow, channel, phone_number)
                if not user:
                    logger.warning("actionable_consumer_user_not_found", phone_number=phone_number, channel=channel)
                    return

                existing = await uow.actionable_messages.get_by_channel_message_id_for_user(
                    channel_message_id=message_id,
                    user_id=str(user.id),
                )
                if existing:
                    logger.info(
                        "actionable_message_duplicate_ignored",
                        user_id=str(user.id),
                        message_id=message_id,
                    )
                    return

                msg_type = (
                    ActionableMessageTypeEnum.TRANSFER_RECEIPT
                    if "transaction_id" in actionable_payload
                    else ActionableMessageTypeEnum.CONFIRMATION_REQUEST
                )

                # Persist
                new_msg = ActionableMessage(
                    user_id=user.id,
                    channel_message_id=message_id,  # Using this column for both WA and Telegram message IDs
                    message_type=msg_type.value,
                    message_data=actionable_payload,
                    expires_at=datetime.utcnow() + timedelta(days=7),  # Valid for 7 days
                )

                # Use the underlying DB session directly for insertion since repository lacks a generic `create`
                uow.actionable_messages.db.add(new_msg)

                try:
                    await uow.commit()
                except IntegrityError as e:
                    await uow.rollback()
                    if _is_duplicate_channel_message_error(e):
                        logger.info("actionable_message_duplicate_ignored", message_id=message_id)
                        return
                    raise
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
