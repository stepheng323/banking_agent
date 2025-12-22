"""WhatsApp webhook service - business logic for handling WhatsApp messages."""
from datetime import datetime
from typing import List, Optional

from shared.clients.whatsapp.client import WhatsAppClient
from shared.models.messages import MessagePriority, MessageType, WhatsAppMessage
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

from apps.gateway.adapters.meta_whatsapp import parse_payload
from apps.gateway.adapters.sender import send_text

logger = get_logger(__name__)


# Temporary whitelist for testing
ALLOWED_NUMBERS = {"2348162511023"}


class WhatsAppWebhookService:
    """Handles business logic for WhatsApp webhook events."""

    def __init__(
        self,
        queue: RedisQueue,
        whatsapp_client: WhatsAppClient,
    ):
        self.queue = queue
        self.whatsapp_client = whatsapp_client

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

    async def _process_message(self, msg: dict) -> bool:
        """Process a single message. Returns True if processed."""
        from_id = msg["from"]
        msg_type = msg.get("type", "text")
        flow_data = msg.get("flow_data")

        # Whitelist check
        if from_id not in ALLOWED_NUMBERS:
            logger.debug("webhook_message_filtered", from_id=from_id)
            return False

        logger.info("webhook_message_received", from_id=from_id, msg_type=msg_type)

        # Determine if this message should be processed here
        is_regular_message = msg_type in ("text", "image", "audio")
        is_interactive_without_flow = msg_type == "interactive" and not flow_data

        if not (is_regular_message or is_interactive_without_flow):
            if msg_type == "interactive" and flow_data:
                logger.debug("flow_response_skipped", from_id=from_id)
            return False

        # Build message object
        whatsapp_msg = self._build_message(msg)
        
        # Enqueue for processing
        return await self._enqueue_message(whatsapp_msg, from_id, msg_type)

    def _build_message(self, msg: dict) -> WhatsAppMessage:
        """Build WhatsAppMessage from raw message dict."""
        msg_type = msg.get("type", "text")
        
        try:
            enum_type = MessageType(msg_type)
        except ValueError:
            enum_type = MessageType.TEXT

        priority = MessagePriority.HIGH if msg_type == "interactive" else MessagePriority.NORMAL

        return WhatsAppMessage(
            message_id=msg.get("id", "unknown"),
            from_number=msg["from"],
            message_type=enum_type,
            text=msg.get("text") or "",
            flow_data=msg.get("flow_data"),
            media_id=msg.get("media_id"),
            mime_type=msg.get("mime_type"),
            timestamp=datetime.utcnow(),
            priority=priority,
        )

    async def _enqueue_message(
        self,
        message: WhatsAppMessage,
        from_id: str,
        msg_type: str,
    ) -> bool:
        """Enqueue message for processing. Returns True on success."""
        try:
            await self.queue.enqueue_simple(
                queue_name="banking:messages",
                message=message.model_dump(mode="json"),
            )
            logger.info("message_enqueued", msg_type=msg_type, from_id=from_id)

            # Send typing indicator for non-interactive messages
            if msg_type != "interactive":
                try:
                    await self.whatsapp_client.send_typing_indicator(
                        message_id=message.message_id
                    )
                except Exception:
                    pass  # Typing indicator is not critical

            return True

        except Exception as e:
            logger.error("message_enqueue_failed", error=str(e))
            await send_text(
                to=from_id,
                text="Sorry, I'm having trouble processing your message right now.",
            )
            return False
