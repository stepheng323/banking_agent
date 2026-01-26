"""WhatsApp webhook service - business logic for handling WhatsApp messages."""

from datetime import datetime

from apps.gateway.adapters.meta_whatsapp import ParsedMessage, parse_payload
from shared.queue.messages import OUTBOX_QUEUE
from shared.models.messages import MessagePriority, MessageType, WhatsAppMessage
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


ALLOWED_NUMBERS = {"2348162511023"}


class WhatsAppWebhookService:
    """Handles business logic for WhatsApp webhook events."""

    def __init__(
        self,
        queue: RedisQueue,
    ):
        self.queue = queue

    async def process_payload(self, payload: dict) -> int:
        """
        Process WhatsApp webhook payload.

        Returns number of messages processed.
        """
        messages = parse_payload(payload)
        processed = 0

        for msg in messages:
            if await self._process_message(msg):
                processed += 1

        return processed

    async def _process_message(self, msg: ParsedMessage) -> bool:
        """Process a single message. Returns True if processed."""
        from_id = msg.from_number
        msg_type = msg.type or "text"
        flow_data = msg.flow_data

        # Whitelist check
        if from_id not in ALLOWED_NUMBERS:
            logger.debug("webhook_message_filtered", from_id=from_id)
            return False

        logger.info("webhook_message_received", from_id=from_id, msg_type=msg_type)

        is_regular_message = msg_type in ("text", "image", "audio")
        is_interactive_without_flow = msg_type == "interactive" and not flow_data

        if not (is_regular_message or is_interactive_without_flow):
            if msg_type == "interactive" and flow_data:
                logger.debug("flow_response_skipped", from_id=from_id)
            return False

        whatsapp_msg = self._build_message(msg)
        await self._enqueue_message(whatsapp_msg, from_id, msg_type)
        return True

    def _build_message(self, msg: ParsedMessage) -> WhatsAppMessage:
        """Build WhatsAppMessage from ParsedMessage."""
        msg_type = msg.type or "text"

        try:
            enum_type = MessageType(msg_type)
        except ValueError:
            enum_type = MessageType.TEXT

        priority = MessagePriority.HIGH if msg_type == "interactive" else MessagePriority.NORMAL

        return WhatsAppMessage(
            message_id=msg.id or "unknown",
            from_number=msg.from_number or "",
            message_type=enum_type,
            text=msg.text or "",
            flow_data=msg.flow_data,
            media_id=msg.media_id,
            mime_type=msg.mime_type,
            quoted_message_id=msg.quoted.message_id if msg.quoted else None,
            timestamp=datetime.utcnow(),
            priority=priority,
            channel="whatsapp",
        )

    async def _enqueue_message(
        self,
        message: WhatsAppMessage,
        from_id: str,
        msg_type: str,
    ) -> bool:
        """Enqueue message for processing. Returns True on success."""
        try:
            await self.queue.enqueue(
                queue_name="banking:messages",
                message=message.model_dump(mode="json"),
            )
            logger.info("message_enqueued", msg_type=msg_type, from_id=from_id)
            return True

        except Exception as e:
            logger.error("message_enqueue_failed", error=str(e))
            try:
                await self.queue.enqueue(
                    queue_name=OUTBOX_QUEUE,
                    message={
                        "phone_number": from_id,
                        "channel": "whatsapp",
                        "intents": [{"type": "say", "text": "Sorry, I'm having trouble processing your message right now."}],
                        "metadata": {"source": "whatsapp_webhook", "reason": "enqueue_failed"},
                    },
                )
            except Exception as enqueue_error:
                logger.error("outbox_enqueue_failed", error=str(enqueue_error))
            return False
