"""Message consumer for processing queued messages."""

import asyncio
from typing import Any

from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowReceipt,
    UiIntent,
)
from apps.core.src.messaging.presenters.base import PresentationContext
# PresenterFactory removed
from shared.cache.rate_limiter import message_rate_limiter
from shared.clients.abstractions.messaging import MessagingClient
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
from shared.queue.messages import OUTBOX_QUEUE
from shared.queue.redis_queue import RedisQueue
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger
from shared.utils.sanitize import is_suspicious_input, sanitize_message

logger = get_logger(__name__)


class MessageConsumer:
    """Message Consumer Class"""

    def __init__(
        self,
        redis_queue: RedisQueue,
        user_repository: UserRepository,
        onboarding_executor: OnboardingExecutor,
        orchestrator: OrchestratorAgent,
        messaging_client: MessagingClient,
    ):
        self.queue = redis_queue
        self.user_repository = user_repository
        self.onboarding_executor = onboarding_executor
        self.orchestrator = orchestrator
        self.messaging_client = messaging_client
        self.running = False

    async def process_message(self, message_data: dict) -> None:
        """Process a message from the queue."""
        try:
            msg = WhatsAppMessage(**message_data)
            await self._handle_message(msg)

        except Exception as e:
            logger.error("message_processing_failed", error=str(e), exc_info=True)
            raise e

    async def _handle_message(self, message: WhatsAppMessage) -> dict[str, Any] | None:
        """Handle a WhatsApp message."""
        phone_number = message.from_number

        rate_result = await message_rate_limiter.check(phone_number)
        if not rate_result.allowed:
            logger.warning(
                "rate_limit_blocked",
                phone_number=phone_number,
                reset_in=rate_result.reset_in_seconds,
            )
            await self.messaging_client.send_text(
                phone_number,
                f"⏳ Too many messages. Please wait {rate_result.reset_in_seconds} seconds.",
            )
            return {"status": "rate_limited", "reset_in": rate_result.reset_in_seconds}

        raw_text = message.text or ""
        sanitized_text = sanitize_message(raw_text)

        if is_suspicious_input(sanitized_text):
            logger.warning(
                "suspicious_input_detected",
                phone_number=phone_number,
                text_preview=sanitized_text[:100],
            )

        if message.message_type.value == "flow":
            return {"status": "skipped", "reason": "Flow messages handled by flow webhook"}

        user = await self.user_repository.get_by_phone(phone_number)
        logger.info("user_found", user=user, phone_number=phone_number)
        if user is None or getattr(user, "onboarding_status", None) != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return await self.onboarding_executor.handle_onboarding(message)

        await self.orchestrator.context_manager.save_message_id(phone_number, message.message_id)

        orchestrator_output = await self.orchestrator.invoke(
            phone_number,
            sanitized_text,
            message.message_id,
            message_type=message.message_type.value,
            media_id=message.media_id,
            quoted_message_id=message.quoted_message_id,
            channel=message.channel,
        )

        # Intents are now converted by the Orchestrator
        intents: list[UiIntent] = orchestrator_output.get("intents", [])

        # Ensure text is covered
        response_text = orchestrator_output.get("text")
        has_primary_interaction = any(isinstance(i, (RequestAuth, RequestConfirmation, ShowReceipt)) for i in intents)
        if response_text and not has_primary_interaction and not any(isinstance(i, Say) for i in intents):
            intents.append(Say(text=response_text))

        if intents:
            serialized_intents = [i.to_dict() for i in intents]
            
            await self.queue.enqueue(
                queue_name=OUTBOX_QUEUE,
                message={
                    "phone_number": phone_number,
                    "channel": message.channel,
                    "intents": serialized_intents,
                    "metadata": {"source": "message_consumer", "message_id": message.message_id}
                }
            )
            logger.info("message_consumer_enqueued_outbox", count=len(intents))

        return {"status": "success", "response": response_text}

    async def start(self, queue_name: str = "banking:messages"):
        """Start the message consumer."""
        self.running = True
        logger.info("message_consumer_starting", queue=queue_name)

        await self.queue.connect()
        while self.running:
            try:
                message_data = await self.queue.dequeue_blocking(queue_name=queue_name, timeout=5)
                if message_data:
                    await self.process_message(message_data)

            except asyncio.CancelledError:
                logger.info("message_consumer_cancelled")
                break
            except Exception as e:
                logger.error("message_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)

        await self.queue.close()
        logger.info("message_consumer_stopped")

    def stop(self):
        """Stop the message consumer."""
        self.running = False
