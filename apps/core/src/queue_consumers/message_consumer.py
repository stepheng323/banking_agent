"""Message consumer for processing queued messages."""

import asyncio
from typing import Any

from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.orchestrator import OrchestratorAgent
from shared.cache.rate_limiter import message_rate_limiter
from shared.clients.whatsapp.client import WhatsAppClient
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
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
        whatsapp_client: WhatsAppClient,
    ):
        self.queue = redis_queue
        self.user_repository = user_repository
        self.onboarding_executor = onboarding_executor
        self.orchestrator = orchestrator
        self.whatsapp_client = whatsapp_client
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

        # Rate limiting - prevent spam attacks
        rate_result = await message_rate_limiter.check(phone_number)
        if not rate_result.allowed:
            logger.warning(
                "rate_limit_blocked",
                phone_number=phone_number,
                reset_in=rate_result.reset_in_seconds,
            )
            await self.whatsapp_client.send_text(
                phone_number,
                f"⏳ Too many messages. Please wait {rate_result.reset_in_seconds} seconds.",
            )
            return {"status": "rate_limited", "reset_in": rate_result.reset_in_seconds}

        # Sanitize input - protect against malformed/malicious input
        raw_text = message.text or ""
        sanitized_text = sanitize_message(raw_text)

        # Log suspicious input for monitoring (but still process)
        if is_suspicious_input(sanitized_text):
            logger.warning(
                "suspicious_input_detected",
                phone_number=phone_number,
                text_preview=sanitized_text[:100],
            )

        if message.message_type.value == "flow":
            return {"status": "skipped", "reason": "Flow messages handled by flow webhook"}

        user = await asyncio.to_thread(self.user_repository.get_by_phone, phone_number)
        if (
            user is None
            or getattr(user, "onboarding_status", None)
            != UserOnboardingStatusEnum.ONBOARDING_COMPLETED
        ):
            return await self.onboarding_executor.handle_onboarding(message)

        await self.orchestrator.context_manager.load_user_context(phone_number, user=user)

        await self.orchestrator.context_manager.save_message_id(phone_number, message.message_id)

        response = await self.orchestrator.invoke(
            phone_number,
            sanitized_text,  # Use sanitized text instead of raw
            message.message_id,
            message_type=message.message_type.value,
            media_id=message.media_id,
            quoted_message_id=message.quoted_message_id,
        )

        if response and response.strip():
            await self.whatsapp_client.send_text(
                phone_number, response, message_id=message.message_id
            )

        return {"status": "success", "response": response}

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
