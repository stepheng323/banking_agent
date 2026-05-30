"""WhatsApp webhook service - business logic for handling WhatsApp messages."""

from apps.gateway.adapters.meta_whatsapp import ParsedMessage, parse_payload
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
from shared.models.messages import ChannelMessage, MessagePriority, MessageType
from shared.queue.adapter import QueuePublisher
from shared.utils.datetime import utc_now_naive
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)
_CHANNEL_LINK_APPROVE_PREFIX = "ch_link_ok:"
_CHANNEL_LINK_DENY_PREFIX = "ch_link_no:"


def _normalize_whatsapp_number(value: str | None) -> str:
    if not value:
        return ""

    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("234") and len(digits) == 13:
        return f"0{digits[3:]}"
    return digits


def _iter_status_callbacks(payload: dict) -> list[dict]:
    statuses: list[dict] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for status in value.get("statuses", []) or []:
                if isinstance(status, dict):
                    statuses.append(status)
    return statuses


class WhatsAppWebhookService:
    """Handles business logic for WhatsApp webhook events."""

    def __init__(
        self,
        publisher: QueuePublisher,
        whatsapp_client: WhatsAppClient,
    ):
        self.publisher = publisher
        self.whatsapp_client = whatsapp_client

    async def process_payload(self, payload: dict) -> int:
        """
        Process WhatsApp webhook payload.

        Returns number of messages processed.
        """
        messages = parse_payload(payload)
        processed = 0

        if not messages:
            statuses = _iter_status_callbacks(payload)
            if statuses:
                for status in statuses:
                    errors = status.get("errors") or []
                    logger.info(
                        "webhook_status_callback_received",
                        message_id_hash=log_fingerprint(status.get("id")),
                        recipient_id_hash=log_fingerprint(status.get("recipient_id")),
                        status=status.get("status"),
                        error_codes=[
                            error.get("code") for error in errors if isinstance(error, dict) and error.get("code")
                        ],
                        error_titles=[
                            error.get("title") for error in errors if isinstance(error, dict) and error.get("title")
                        ],
                        error_details=[
                            (error.get("error_data") or {}).get("details")
                            for error in errors
                            if isinstance(error, dict) and isinstance(error.get("error_data"), dict)
                        ],
                    )
            else:
                logger.info("webhook_no_messages_parsed")
            return 0

        for msg in messages:
            if await self._process_message(msg):
                processed += 1

        logger.info("webhook_messages_processed", parsed_count=len(messages), processed_count=processed)

        return processed

    async def _process_message(self, msg: ParsedMessage) -> bool:
        """Process a single message. Returns True if processed."""
        from_id = msg.from_number
        normalized_from_id = _normalize_whatsapp_number(from_id)
        if not from_id:
            logger.info("webhook_message_missing_sender")
            return False
        msg_type = msg.type or "text"
        flow_data = msg.flow_data

        allowed_numbers = {
            _normalize_whatsapp_number(number)
            for number in settings.whatsapp.allowed_numbers
            if _normalize_whatsapp_number(number)
        }
        if allowed_numbers and normalized_from_id not in allowed_numbers:
            logger.info(
                "webhook_message_filtered_by_number_gate",
                from_id=from_id,
                normalized_from_id=normalized_from_id,
                allowed_numbers=sorted(allowed_numbers),
            )
            return False

        logger.info("webhook_message_received", from_id=from_id, msg_type=msg_type)

        is_regular_message = msg_type in ("text", "image", "audio")
        is_interactive_without_flow = msg_type == "interactive" and not flow_data

        if not (is_regular_message or is_interactive_without_flow):
            if msg_type == "interactive" and flow_data:
                interactive = msg.raw.get("interactive", {}) if isinstance(msg.raw, dict) else {}
                logger.info(
                    "whatsapp_flow_message_response_skipped",
                    from_id_hash=log_fingerprint(from_id),
                    interactive_type=interactive.get("type") if isinstance(interactive, dict) else None,
                    flow_data_keys=sorted(str(key) for key in flow_data),
                )
            return False

        if msg.text and await self._handle_channel_link_authorization(msg.text.strip(), from_id):
            return True

        whatsapp_msg = self._build_message(msg)
        return await self._enqueue_message(whatsapp_msg, from_id, msg_type)

    async def _handle_channel_link_authorization(self, text: str, from_id: str) -> bool:
        """Neutralize stale native channel-link buttons; PIN is required now."""
        if not (text.startswith(_CHANNEL_LINK_APPROVE_PREFIX) or text.startswith(_CHANNEL_LINK_DENY_PREFIX)):
            return False

        await self.whatsapp_client.send_text(
            to=from_id,
            text="For security, channel linking now requires PIN authorization. Please use the latest PIN prompt.",
            suppress_typing_indicator=True,
        )
        return True

    async def _notify_requested_channel_linked(self, channel: str, channel_user_id: str) -> None:
        if channel != "telegram":
            return
        try:
            from shared.clients.telegram.client import TelegramClient

            await TelegramClient().send_text(
                to=channel_user_id,
                text="Your Telegram account has been linked. You can now use banking features here.",
            )
        except Exception as e:
            logger.warning("channel_link_requested_channel_notify_failed", channel=channel, error=str(e))

    def _build_message(self, msg: ParsedMessage) -> ChannelMessage:
        """Build ChannelMessage from ParsedMessage."""
        msg_type = msg.type or "text"

        try:
            enum_type = MessageType(msg_type)
        except ValueError:
            enum_type = MessageType.TEXT

        priority = MessagePriority.HIGH if msg_type == "interactive" else MessagePriority.NORMAL
        channel_metadata = {}
        if msg.contact_profile_name:
            channel_metadata["sender_display_name"] = msg.contact_profile_name

        return ChannelMessage(
            message_id=msg.id or "",
            channel_user_id=msg.from_number or "",
            message_type=enum_type,
            text=msg.text or "",
            flow_data=msg.flow_data,
            media_id=msg.media_id,
            mime_type=msg.mime_type,
            quoted_message_id=msg.quoted.message_id if msg.quoted else None,
            channel_metadata=channel_metadata,
            timestamp=utc_now_naive(),
            priority=priority,
        )

    async def _enqueue_message(
        self,
        message: ChannelMessage,
        from_id: str,
        msg_type: str,
    ) -> bool:
        """Enqueue message for processing. Returns True on success."""
        try:
            await self.publisher.publish(
                topic="message.received",
                message=message.model_dump(mode="json"),
            )
            logger.info("message_enqueued", msg_type=msg_type, from_id=from_id)
            return True

        except Exception as e:
            logger.error("message_enqueue_failed", error=str(e))
            await self.whatsapp_client.send_text(
                to=from_id,
                text="Sorry, I'm having trouble processing your message right now.",
                suppress_typing_indicator=True,
            )
            return False
