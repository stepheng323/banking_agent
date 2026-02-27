"""Message consumer for processing queued messages."""

import asyncio
import time
from typing import Any, cast

from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.models.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowOptions,
    ShowReceipt,
    UiIntent,
)
from apps.core.src.messaging.outbox import enqueue_outbox_intents, enqueue_outbox_say
from shared.cache.rate_limiter import message_rate_limiter
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import ChannelMessage
from shared.queue.adapter import QueueConsumer, QueuePublisher
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger
from shared.utils.sanitize import is_suspicious_input, sanitize_message

logger = get_logger(__name__)


class _NoopPublisher:
    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        del topic, message


class MessageConsumer:
    """Message Consumer Class"""

    def __init__(
        self,
        user_repository: UserRepository,
        onboarding_executor: OnboardingExecutor,
        orchestrator: OrchestratorAgent,
        publisher: QueuePublisher | None = None,
        queue_consumer: QueueConsumer | None = None,
    ):
        self.queue_consumer = queue_consumer
        self.publisher = publisher or _NoopPublisher()
        self.user_repository = user_repository
        self.onboarding_executor = onboarding_executor
        self.orchestrator = orchestrator
        self.running = False

    async def process_message(self, message_data: dict) -> None:
        """Process a message from the queue."""
        # Refresh the database session state so we don't read stale cached data
        await self.user_repository.db.rollback()

        try:
            msg = ChannelMessage(**message_data)
            await self._handle_message(msg)
            # Commit any changes pushed by the orchestrator into the session
            await self.user_repository.db.commit()

        except Exception as e:
            await self.user_repository.db.rollback()
            logger.error("message_processing_failed", error=str(e), exc_info=True)
            raise e

    async def _handle_message(self, message: ChannelMessage) -> dict[str, Any] | None:
        """Handle a Channel message."""
        start_time = time.perf_counter()
        channel_user_id = message.channel_user_id

        rate_result = await message_rate_limiter.check(channel_user_id)
        if not rate_result.allowed:
            logger.warning(
                "rate_limit_blocked",
                channel_user_id=channel_user_id,
                reset_in=rate_result.reset_in_seconds,
            )
            await enqueue_outbox_say(
                self.publisher,
                channel_user_id,
                message.channel,
                f"⏳ Too many messages. Please wait {rate_result.reset_in_seconds} seconds.",
                metadata={"source": "message_consumer", "reason": "rate_limit"},
            )
            return {"status": "rate_limited", "reset_in": rate_result.reset_in_seconds}

        raw_text = message.text or ""
        sanitized_text = sanitize_message(raw_text)

        if is_suspicious_input(sanitized_text):
            logger.warning(
                "suspicious_input_detected",
                channel_user_id=channel_user_id,
                text_preview=sanitized_text[:100],
            )

        if message.message_type.value == "flow":
            return {"status": "skipped", "reason": "Flow messages handled by flow webhook"}

        user = await self.user_repository.get_by_channel_identity(message.channel, channel_user_id)
        logger.info("channel_identity_lookup", user=user, channel=message.channel, channel_user_id=channel_user_id)

        if user is None or getattr(user, "onboarding_status", None) != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return cast(dict[str, Any] | None, await self.onboarding_executor.handle_onboarding(message))

        # Use the real phone number from the database for the orchestrator.
        # For WhatsApp, channel_user_id == phone_number, but for Telegram
        # channel_user_id is a chat ID which would break account lookups.
        phone_number = user.phone_number

        claimed_message = await self.orchestrator.context_manager.claim_inbound_message(
            phone_number,
            message.message_id,
        )
        if not claimed_message:
            logger.info("duplicate_inbound_message_ignored", phone_number=phone_number, message_id=message.message_id)
            return {"status": "duplicate_ignored", "message_id": message.message_id}

        response_text: str | None = None
        try:
            await self.orchestrator.context_manager.save_message_id(phone_number, message.message_id)

            orchestrator_output = await self.orchestrator.invoke(
                phone_number,
                sanitized_text,
                message.message_id,
                message_type=message.message_type.value,
                media_id=message.media_id,
                quoted_message_id=message.quoted_message_id,
                channel=message.channel,
                channel_identity=channel_user_id,
            )

            intents: list[UiIntent] = orchestrator_output.get("intents", [])

            response_text = orchestrator_output.get("text")
            has_primary_interaction = any(
                isinstance(i, (RequestAuth, RequestConfirmation, ShowReceipt, ShowOptions)) for i in intents
            )
            if response_text and not has_primary_interaction and not any(isinstance(i, Say) for i in intents):
                intents.append(Say(text=response_text))

            if intents:
                await enqueue_outbox_intents(
                    self.publisher,
                    channel_user_id,
                    message.channel,
                    cast(list[UiIntent | dict[str, Any]], intents),
                    metadata={"source": "message_consumer", "message_id": message.message_id},
                )
                logger.info("message_consumer_enqueued_outbox", count=len(intents))
        except Exception:
            await self.orchestrator.context_manager.release_inbound_message_claim(phone_number, message.message_id)
            raise

        duration = (time.perf_counter() - start_time) * 1000
        logger.info(
            "perf_timer_latency",
            gate="message_consumer_handle",
            duration_ms=round(duration, 2),
            channel_user_id=channel_user_id,
        )

        return {"status": "success", "response": response_text}

    async def start(self, queue_name: str = "banking:messages") -> None:
        """Start the message consumer."""
        if self.queue_consumer is None:
            raise RuntimeError("message_consumer_requires_queue_consumer")
        self.running = True
        logger.info("message_consumer_starting", queue=queue_name)
        while self.running:
            try:
                message_data = await self.queue_consumer.consume_one(queue_name=queue_name, timeout=5)
                if message_data:
                    await self.process_message(message_data)

            except asyncio.CancelledError:
                logger.info("message_consumer_cancelled")
                break
            except Exception as e:
                logger.error("message_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)
        logger.info("message_consumer_stopped")

    def stop(self) -> None:
        """Stop the message consumer."""
        self.running = False
