"""Telegram webhook service — business logic for handling incoming Telegram updates."""

import json
from datetime import UTC, datetime
from typing import Any, cast

from apps.gateway.adapters.telegram import ParsedTelegramMessage, parse_update
from shared.clients.telegram.client import TelegramClient
from shared.config.settings import settings
from shared.models.messages import ChannelMessage, MessagePriority, MessageType
from shared.queue.messages import FlowEvent, FlowEventType
from shared.queue.models import FlowEventPayload
from shared.queue.redis_queue import RedisQueue
from shared.repositories.user_repository import UserRepository
from shared.services.onboarding import session_manager
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TelegramWebhookService:
    """Handles business logic for Telegram webhook events."""

    def __init__(self, queue: RedisQueue, user_repository: UserRepository, telegram_client: TelegramClient) -> None:
        self.queue = queue
        self.user_repository = user_repository
        self.telegram_client = telegram_client

    async def process_update(self, update: dict[str, Any]) -> bool:
        """Process a single Telegram update. Returns True if handled."""
        parsed = parse_update(update)
        if not parsed:
            logger.debug("telegram_update_ignored", update_id=update.get("update_id"))
            return False

        logger.info(
            "telegram_message_received",
            chat_id=parsed.chat_id,
            msg_type=parsed.type,
        )

        if parsed.type == "web_app_data":
            return await self._handle_web_app_data(parsed)
        elif parsed.type == "callback_query":
            return await self._handle_callback_query(parsed)
        elif parsed.type in ("text", "photo", "audio", "contact"):
            return await self._handle_regular_message(parsed)

        return False

    async def _handle_regular_message(self, msg: ParsedTelegramMessage) -> bool:
        """Process text/photo/audio/contact messages.
        Intercepts contacts for linking and blocks unlinked users."""

        # 1. Handle explicit contact sharing (Identity Linking)
        if msg.type == "contact" and msg.contact_phone_number:
            return await self._handle_contact_share(msg)

        user = await self.user_repository.get_by_channel_identity("telegram", msg.chat_id)
        if not user:
            # Check if they are currently in the middle of onboarding
            from shared.services.onboarding import session_manager

            session = await session_manager.get_session(f"onboarding-{msg.chat_id}")
            if session and session.get("phone_number") and session.get("step") != "complete":
                # They already shared their contact, they just need to finish the Mini App
                logger.info("telegram_unlinked_user_onboarding", chat_id=msg.chat_id)
                import time

                from shared.config.settings import settings

                app_url = (
                    f"{settings.telegram_mini_app_base_url}/static/telegram/onboarding.html"
                    f"?flow_token=onboarding-{msg.chat_id}&v={int(time.time())}"
                )
                cta_result = await self.telegram_client._call(
                    "sendMessage",
                    {
                        "chat_id": msg.chat_id,
                        "text": "Please tap the button below to finish creating your account! 🚀",
                        "reply_markup": {
                            "inline_keyboard": [[{"text": "🛠 Continue Setup", "web_app": {"url": app_url}}]]
                        },
                    },
                )
                # Store the CTA message_id so we can disable the button after completion
                cta_msg_id = (cta_result or {}).get("result", {}).get("message_id")
                if cta_msg_id:
                    await session_manager.update_session(
                        f"onboarding-{msg.chat_id}",
                        {"cta_message_id": str(cta_msg_id), "cta_chat_id": msg.chat_id},
                    )
                return True

            logger.info("telegram_unlinked_user_blocked", chat_id=msg.chat_id)
            await self._request_contact(msg.chat_id)
            return True  # Handled (by blocking)

        if msg.text and msg.text.strip() == "/start":
            msg.text = "hi"

        if msg.text and msg.text.startswith("/"):
            msg.text = msg.text.lstrip("/").strip()

        # 5. Show typing indicator immediately so user sees activity while LLM processes
        await self.telegram_client.send_typing_indicator(msg.chat_id)

        # 6. User is linked, enqueue normal message
        message = self._build_message(msg)
        return await self._enqueue(message, msg.chat_id, msg.type)

    async def _handle_contact_share(self, msg: ParsedTelegramMessage) -> bool:
        """Link a shared contact to a core user profile."""
        existing_user = await self.user_repository.get_by_channel_identity("telegram", msg.chat_id)
        if existing_user:
            await self.telegram_client._call(
                "sendMessage",
                {
                    "chat_id": msg.chat_id,
                    "text": "✅ Your Telegram account is already linked to your banking profile!",
                    "reply_markup": {"remove_keyboard": True},
                },
            )
            return True

        phone = str(msg.contact_phone_number).replace("+", "")
        user = await self.user_repository.get_by_phone(phone)

        if user:
            logger.info("linking_telegram_identity", user_id=user.id, chat_id=msg.chat_id)
            await self.user_repository.link_channel_identity(str(user.id), "telegram", msg.chat_id)
            await self.telegram_client._call(
                "sendMessage",
                {
                    "chat_id": msg.chat_id,
                    "text": "✅ Your Telegram account has been successfully linked to your banking profile!",
                    "reply_markup": {"remove_keyboard": True},
                },
            )
        else:
            # We don't have a profile for this phone. They are a brand new user.
            # Pre-seed the onboarding session with the real phone number so
            # downstream services (bvn_verification, account_linking) can find it.

            flow_token = f"onboarding-{msg.chat_id}"
            await session_manager.update_session(flow_token, {"phone_number": phone})

            import time

            app_url = (
                f"{settings.telegram_mini_app_base_url}/static/telegram/onboarding.html"
                f"?flow_token={flow_token}&v={int(time.time())}"
            )

            cta_result = await self.telegram_client._call(
                "sendMessage",
                {
                    "chat_id": msg.chat_id,
                    "text": (
                        "Welcome to Fusepay! 🚀\n\n"
                        "We couldn't find an existing account matching your phone number.\n"
                        "Please click the button below to securely create your new account."
                    ),
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "🛠 Start Setup",
                                    "web_app": {"url": app_url},
                                }
                            ]
                        ]
                    },
                },
            )
            cta_msg_id = (cta_result or {}).get("result", {}).get("message_id")
            if cta_msg_id:
                await session_manager.update_session(
                    flow_token,
                    {"cta_message_id": str(cta_msg_id), "cta_chat_id": msg.chat_id},
                )

        return True

    async def _request_contact(self, chat_id: str) -> None:
        """Send the 'Share Contact' button keyboard."""
        await self.telegram_client._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "Welcome to your Banking Agent! 🏦\n\n"
                    "To access your account, we first need to verify your phone number. "
                    "Please tap the button below to share your contact securely."
                ),
                "reply_markup": {
                    "keyboard": [[{"text": "📱 Share Contact", "request_contact": True}]],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            },
        )

    async def _handle_callback_query(self, msg: ParsedTelegramMessage) -> bool:
        """Process an inline button press — treat as interactive text input."""
        # Silence taps on the 'Authorized' badge — no further action needed
        if msg.text == "auth:done":
            if msg.callback_query_id:
                await self.telegram_client.answer_callback_query(msg.callback_query_id)
            return True

        user = await self.user_repository.get_by_channel_identity("telegram", msg.chat_id)
        if not user:
            logger.info("telegram_unlinked_user_blocked_callback", chat_id=msg.chat_id)
            await self._request_contact(msg.chat_id)
            return True

        message = self._build_message(msg)
        message.priority = MessagePriority.HIGH
        return await self._enqueue(message, msg.chat_id, "interactive")

    async def _handle_web_app_data(self, msg: ParsedTelegramMessage) -> bool:
        """Process data from a Mini App (e.g. PIN entry).

        Publishes a FlowEvent to the flow_events queue so the orchestrator
        picks it up exactly like WhatsApp Flow PIN responses.
        """
        if not msg.web_app_data:
            logger.warning("telegram_empty_web_app_data", chat_id=msg.chat_id)
            return False

        try:
            data: dict[str, Any] = json.loads(msg.web_app_data)
        except json.JSONDecodeError:
            logger.warning("telegram_invalid_web_app_data", chat_id=msg.chat_id)
            return False

        action = data.get("action")
        if action == "onboarding_success":
            # The onboarding flow finished successfully via REST API calls.
            # Acknowledge gently.
            await self.telegram_client.send_text(
                to=msg.chat_id,
                text="🎉 Account setup complete! You can now use all banking features.",
            )
            return True

        flow_token: str = data.get("flow_token", "")
        pin: str = data.get("pin", "")

        if not flow_token:
            logger.warning("telegram_web_app_missing_flow_token", chat_id=msg.chat_id)
            return False

        # Parse flow_token like "transfer-pin-<idempotency_key>-<phone>"
        parts = flow_token.split("-", 2)
        flow_type = parts[0] if len(parts) > 0 else "unknown"

        # Determine the idempotency_key portion
        # Format: "<prefix>-pin-<idempotency_key>-<phone>"
        # We need to extract the idempotency_key + phone from the flow_token
        # The last segment after the last dash is the phone
        token_remainder = flow_token.split("-pin-", 1)[-1] if "-pin-" in flow_token else flow_token

        event = FlowEvent(
            event_type=FlowEventType.PIN_VERIFIED if pin else FlowEventType.PIN_FAILED,
            phone_number=msg.chat_id,
            flow_type=flow_type,
            idempotency_key=token_remainder,
            success=bool(pin),
            channel="telegram",
            extra_data={"pin": pin, "source": "telegram_mini_app"},
        )

        try:
            await self.queue.enqueue(
                queue_name="banking:flow_events",
                message=cast(FlowEventPayload, event.to_dict()),
            )
            logger.info("telegram_pin_event_published", chat_id=msg.chat_id, flow_type=flow_type)
            return True
        except Exception as e:
            logger.error("telegram_pin_event_failed", error=str(e), exc_info=True)
            return False

    def _build_message(self, msg: ParsedTelegramMessage) -> ChannelMessage:
        """Build a ChannelMessage from a Telegram update.

        Re-uses the existing message model (which already has a channel field).
        """
        msg_type_str = msg.type or "text"
        # Map Telegram types to our MessageType enum
        type_map: dict[str, MessageType] = {
            "text": MessageType.TEXT,
            "photo": MessageType.IMAGE,
            "audio": MessageType.AUDIO,
            "callback_query": MessageType.TEXT,  # treat button press as text
            "contact": MessageType.CONTACT,
        }
        enum_type = type_map.get(msg_type_str, MessageType.TEXT)

        metadata = {}
        if msg.contact_phone_number:
            metadata["phone_number"] = msg.contact_phone_number

        return ChannelMessage(
            message_id=str(msg.message_id),
            channel_user_id=msg.chat_id,
            message_type=enum_type,
            text=msg.text or "",
            flow_data=None,
            media_id=msg.photo_file_id or msg.audio_file_id,
            mime_type=None,
            quoted_message_id=msg.quoted_message_id,
            channel_metadata=metadata,
            timestamp=datetime.now(tz=UTC),
            channel="telegram",
            priority=MessagePriority.NORMAL,
        )

    async def _enqueue(
        self,
        message: ChannelMessage,
        chat_id: str,
        msg_type: str,
    ) -> bool:
        """Enqueue message for core processing."""
        try:
            await self.queue.enqueue(
                queue_name="banking:messages",
                message=message.model_dump(mode="json"),
            )
            logger.info("telegram_message_enqueued", msg_type=msg_type, chat_id=chat_id)
            return True
        except Exception as e:
            logger.error("telegram_message_enqueue_failed", error=str(e), exc_info=True)
            return False
