"""Telegram webhook service — business logic for handling incoming Telegram updates."""

import json
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from apps.gateway.adapters.telegram import ParsedTelegramMessage, parse_update
from shared.cache.channel_identity_cache import load_channel_identity_user, store_channel_identity_user
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
from shared.models.messages import ChannelMessage, MessagePriority, MessageType
from shared.queue.adapter import QueuePublisher
from shared.repositories.user_repository import UserRepository
from shared.services.channel_linking import build_channel_link_pin_token
from shared.services.onboarding import OnboardingStep, session_manager
from shared.services.telegram_miniapp_bootstrap import create_telegram_miniapp_bootstrap
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)
_TELEGRAM_CHANNEL = "telegram"
_WHATSAPP_CHANNEL = "whatsapp"
_CHANNEL_LINK_APPROVE_PREFIX = "ch_link_ok:"
_CHANNEL_LINK_DENY_PREFIX = "ch_link_no:"


def _new_onboarding_flow_token() -> str:
    return f"onboarding-{secrets.token_urlsafe(32)}"


def _new_channel_link_token() -> str:
    return f"channel-link-{secrets.token_urlsafe(32)}"


def _telegram_onboarding_token_key(chat_id: str) -> str:
    return f"telegram:onboarding:{chat_id}:flow_token"


async def _store_telegram_onboarding_token(chat_id: str, flow_token: str) -> None:
    await session_manager.redis.set(_telegram_onboarding_token_key(chat_id), flow_token, ex=session_manager.ttl)


async def _get_telegram_onboarding_token(chat_id: str) -> str:
    token = await session_manager.redis.get(_telegram_onboarding_token_key(chat_id))
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return str(token or "")


async def _build_telegram_mini_app_url(*, chat_id: str, flow_token: str, endpoint: str) -> str:
    import time

    nonce = await create_telegram_miniapp_bootstrap(
        chat_id=chat_id,
        flow_token=flow_token,
        endpoint=endpoint,
    )
    endpoint_file = "linking.html" if endpoint == "linking" else "onboarding.html"
    query = urlencode({"boot": nonce, "v": str(int(time.time()))})
    return f"{settings.telegram_mini_app_base_url}/static/telegram/{endpoint_file}?{query}"


def _is_telegram_self_contact(msg: ParsedTelegramMessage) -> bool:
    return (
        bool(msg.contact_phone_number)
        and bool(msg.from_user_id)
        and bool(msg.contact_user_id)
        and msg.chat_id == msg.from_user_id
        and msg.contact_user_id == msg.from_user_id
    )


class TelegramWebhookService:
    """Handles business logic for Telegram webhook events."""

    def __init__(
        self,
        publisher: QueuePublisher,
        user_repository: UserRepository,
        telegram_client: TelegramClient | None = None,
    ) -> None:
        self.publisher = publisher
        self.user_repository = user_repository
        self.telegram_client = telegram_client or TelegramClient()

    async def process_update(self, update: dict[str, Any]) -> bool:
        """Process a single Telegram update. Returns True if handled."""
        parsed = parse_update(update)
        if not parsed:
            logger.info(
                "telegram_update_ignored",
                update_id=update.get("update_id"),
                keys=sorted(update.keys()),
            )
            return False

        logger.info(
            "telegram_message_received",
            chat_id_hash=log_fingerprint(parsed.chat_id),
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

        user = await self._resolve_linked_user(msg.chat_id)
        if not user:
            # Check if they are currently in the middle of onboarding
            from shared.services.onboarding import session_manager

            flow_token = await _get_telegram_onboarding_token(msg.chat_id)
            session = await session_manager.get_session(flow_token) if flow_token else {}
            if session and session.get("phone_number") and session.get("step") != "complete":
                # They already shared their contact, they just need to finish the Mini App
                logger.info("telegram_unlinked_user_onboarding", chat_id_hash=log_fingerprint(msg.chat_id))
                app_url = await _build_telegram_mini_app_url(
                    chat_id=msg.chat_id,
                    flow_token=flow_token,
                    endpoint="onboarding",
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
                        flow_token,
                        {"cta_message_id": str(cta_msg_id), "cta_chat_id": msg.chat_id},
                    )
                return True

            logger.info("telegram_unlinked_user_blocked", chat_id_hash=log_fingerprint(msg.chat_id))
            await self._request_contact(msg.chat_id)
            return True  # Handled (by blocking)

        if msg.text and msg.text.strip() == "/start":
            msg.text = "hi"

        if msg.text and msg.text.startswith("/"):
            msg.text = msg.text.lstrip("/").strip()

        # 5. User is linked, enqueue normal message
        message = self._build_message(msg)
        return await self._enqueue(message, msg.chat_id, msg.type)

    async def _handle_contact_share(self, msg: ParsedTelegramMessage) -> bool:
        """Link a shared contact to a core user profile."""
        existing_user = await self._resolve_linked_user(msg.chat_id)
        if existing_user:
            await self.telegram_client._call(
                "sendMessage",
                {
                    "chat_id": msg.chat_id,
                    "text": "✓ Your Telegram account is already linked to your banking profile.",
                    "reply_markup": {"remove_keyboard": True},
                },
            )
            return True

        if not _is_telegram_self_contact(msg):
            logger.warning(
                "telegram_contact_share_rejected",
                chat_id_hash=log_fingerprint(msg.chat_id),
                from_user_id_hash=log_fingerprint(msg.from_user_id),
                contact_user_id_hash=log_fingerprint(msg.contact_user_id),
            )
            await self.telegram_client._call(
                "sendMessage",
                {
                    "chat_id": msg.chat_id,
                    "text": "Please tap the Share Contact button and share your own Telegram phone number.",
                    "reply_markup": {"remove_keyboard": True},
                },
            )
            return True

        phone = str(msg.contact_phone_number).replace("+", "")
        user = await self.user_repository.get_by_phone(phone)

        if user:
            logger.info("telegram_identity_link_requires_existing_channel_authorization", user_id=user.id)
            flow_token = _new_channel_link_token()
            authorizing_identity = ""
            if hasattr(self.user_repository, "get_channel_identity_by_phone"):
                authorizing_identity = (
                    await self.user_repository.get_channel_identity_by_phone(phone, _WHATSAPP_CHANNEL) or ""
                )
            authorizing_identity = authorizing_identity or phone

            stored = await session_manager.update_session_strict(
                flow_token,
                {
                    "purpose": "channel_identity_link",
                    "user_id": str(user.id),
                    "phone_number": phone,
                    "requested_channel": _TELEGRAM_CHANNEL,
                    "requested_channel_user_id": msg.chat_id,
                    "requested_channel_actor_id": msg.from_user_id,
                    "authorizing_channel": _WHATSAPP_CHANNEL,
                    "authorizing_channel_user_id": authorizing_identity,
                    "step": "pending_existing_channel_authorization",
                },
                verify=True,
            )
            if not stored:
                logger.error("telegram_identity_link_session_store_failed")
                await self.telegram_client._call(
                    "sendMessage",
                    {
                        "chat_id": msg.chat_id,
                        "text": "I couldn't start that link request. Please try again.",
                        "reply_markup": {"remove_keyboard": True},
                    },
                )
                return True

            try:
                logger.info(
                    "telegram_identity_link_whatsapp_pin_flow_send",
                    flow_token_hash=log_fingerprint(flow_token),
                    pin_flow_token_hash=log_fingerprint(build_channel_link_pin_token(flow_token)),
                    authorizing_identity_hash=log_fingerprint(authorizing_identity),
                    requested_telegram_hash=log_fingerprint(msg.chat_id),
                    flow_id=settings.whatsapp.pin_confirmation_flow_id,
                )
                flow_result = await WhatsAppClient().send_flow(
                    to=authorizing_identity,
                    flow_id=settings.whatsapp.pin_confirmation_flow_id,
                    flow_config={
                        "header": "Authorize Telegram link",
                        "text_body": (
                            "Enter your transaction PIN to link Telegram to your banking profile. "
                            "Continue only if this request was from you."
                        ),
                        "flow_cta": "Enter PIN",
                        "screen_name": "Pin",
                        "flow_token": build_channel_link_pin_token(flow_token),
                        "flow_action_payload": {"screen": "Pin"},
                    },
                    suppress_typing_indicator=True,
                )
                if not flow_result.success:
                    raise RuntimeError(flow_result.error or "WhatsApp PIN flow send failed")
                logger.info(
                    "telegram_identity_link_whatsapp_pin_flow_sent",
                    flow_token_hash=log_fingerprint(flow_token),
                    message_id_hash=log_fingerprint(getattr(flow_result, "message_id", None)),
                    requested_telegram_hash=log_fingerprint(msg.chat_id),
                )
            except Exception as e:
                logger.error("telegram_identity_link_authorization_send_failed", error=str(e))
                await session_manager.delete_session(flow_token)
                await self.telegram_client._call(
                    "sendMessage",
                    {
                        "chat_id": msg.chat_id,
                        "text": "I couldn't send the WhatsApp approval request. Please try again.",
                        "reply_markup": {"remove_keyboard": True},
                    },
                )
                return True

            await self.telegram_client._call(
                "sendMessage",
                {
                    "chat_id": msg.chat_id,
                    "text": (
                        "I sent a secure PIN request to your existing WhatsApp channel. "
                        "Enter your PIN there to finish linking Telegram."
                    ),
                    "reply_markup": {"remove_keyboard": True},
                },
            )
        else:
            # We don't have a profile for this phone. They are a brand new user.
            # Pre-seed the onboarding session with the real phone number so
            # downstream services (bvn_verification, account_linking) can find it.

            flow_token = _new_onboarding_flow_token()
            await session_manager.update_session(
                flow_token,
                {
                    "phone_number": phone,
                    "channel": _TELEGRAM_CHANNEL,
                    "channel_user_id": msg.chat_id,
                    "step": OnboardingStep.BVN_ENTRY.value,
                },
            )
            await _store_telegram_onboarding_token(msg.chat_id, flow_token)

            app_url = await _build_telegram_mini_app_url(
                chat_id=msg.chat_id,
                flow_token=flow_token,
                endpoint="onboarding",
            )

            cta_result = await self.telegram_client._call(
                "sendMessage",
                    {
                        "chat_id": msg.chat_id,
                        "text": (
                            f"Welcome to {settings.app_name}! 🚀\n\n"
                            "We couldn't find an existing account matching your phone number.\n"
                            "Please click the button below to securely create your new account."
                        ),
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "Start Onboarding",
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
                    f"Welcome to {settings.app_name}! 🏦\n\n"
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

        if await self._handle_channel_link_authorization(msg):
            return True

        user = await self._resolve_linked_user(msg.chat_id)
        if not user:
            logger.info("telegram_unlinked_user_blocked_callback", chat_id_hash=log_fingerprint(msg.chat_id))
            await self._request_contact(msg.chat_id)
            return True

        if msg.callback_query_id:
            await self.telegram_client.answer_callback_query(msg.callback_query_id)

        message = self._build_message(msg)
        message.priority = MessagePriority.HIGH
        return await self._enqueue(message, msg.chat_id, "interactive")

    async def _handle_channel_link_authorization(self, msg: ParsedTelegramMessage) -> bool:
        """Neutralize stale native channel-link buttons; PIN is required now."""
        text = (msg.text or "").strip()
        if not (text.startswith(_CHANNEL_LINK_APPROVE_PREFIX) or text.startswith(_CHANNEL_LINK_DENY_PREFIX)):
            return False

        if msg.callback_query_id:
            await self.telegram_client.answer_callback_query(msg.callback_query_id)

        await self.telegram_client.send_text(
            to=msg.chat_id,
            text="For security, channel linking now requires PIN authorization. Please use the latest PIN prompt.",
        )
        return True

    async def _notify_requested_channel_linked(self, channel: str, channel_user_id: str) -> None:
        if channel != _WHATSAPP_CHANNEL:
            return
        try:
            await WhatsAppClient().send_text(
                to=channel_user_id,
                text="Your WhatsApp number has been linked. You can now use banking features there.",
                suppress_typing_indicator=True,
            )
        except Exception as e:
            logger.warning("channel_link_requested_channel_notify_failed", channel=channel, error=str(e))

    async def _resolve_linked_user(self, chat_id: str) -> Any | None:
        user = await load_channel_identity_user(_TELEGRAM_CHANNEL, chat_id)
        if user is not None:
            return user
        user = await self.user_repository.get_by_channel_identity(_TELEGRAM_CHANNEL, chat_id)
        if user is not None:
            await store_channel_identity_user(_TELEGRAM_CHANNEL, chat_id, user)
        return user

    async def _handle_web_app_data(self, msg: ParsedTelegramMessage) -> bool:
        """Process data from a Mini App.

        Transaction PIN entry must use the authenticated REST endpoint so the
        gateway verifies and stores a PIN authorization before core resumes.
        """
        if not msg.web_app_data:
            logger.warning("telegram_empty_web_app_data", chat_id_hash=log_fingerprint(msg.chat_id))
            return False

        try:
            data: dict[str, Any] = json.loads(msg.web_app_data)
        except json.JSONDecodeError:
            logger.warning("telegram_invalid_web_app_data", chat_id_hash=log_fingerprint(msg.chat_id))
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

        if data.get("flow_token") or data.get("pin"):
            logger.warning("telegram_legacy_web_app_pin_ignored", chat_id_hash=log_fingerprint(msg.chat_id))
            await self.telegram_client.send_text(
                to=msg.chat_id,
                text="That PIN prompt has expired. Please reopen the secure PIN page and try again.",
            )
            return True

        logger.warning("telegram_web_app_unknown_action", chat_id_hash=log_fingerprint(msg.chat_id), action=action)
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
            await self.publisher.publish(
                topic="message.received",
                message=message.model_dump(mode="json"),
            )
            logger.info("telegram_message_enqueued", msg_type=msg_type, chat_id_hash=log_fingerprint(chat_id))
            return True
        except Exception as e:
            logger.error("telegram_message_enqueue_failed", error=str(e), exc_info=True)
            await self.telegram_client.send_text(
                to=chat_id,
                text="Sorry, I couldn't process that right now. Please try again.",
            )
            return False
