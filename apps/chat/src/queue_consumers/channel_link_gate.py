"""Channel-link authorization gate for inbound chat messages."""

import secrets
from collections.abc import Callable
from typing import Any

from shared.clients.telegram.client import TelegramClient
from shared.messaging.outbox import enqueue_outbox_say
from shared.models.messages import ChannelMessage
from shared.queue.adapter import QueuePublisher
from banking.identity.channel_linking.authorization import CHANNEL_LINK_SESSION_PURPOSE, build_channel_link_pin_token
from banking.accounts.onboarding.runtime import session_manager
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

TELEGRAM_CHANNEL = "telegram"
WHATSAPP_CHANNEL = "whatsapp"


def looks_like_phone_number(value: str) -> bool:
    normalized = value.strip()
    return normalized.isdigit() and 10 <= len(normalized) <= 15


def _new_channel_link_token() -> str:
    return f"channel-link-{secrets.token_urlsafe(32)}"


async def gate_unlinked_whatsapp_identity(
    *,
    message: ChannelMessage,
    user: Any,
    user_repository: Any,
    publisher: QueuePublisher,
    telegram_client_factory: Callable[[], TelegramClient],
) -> dict[str, Any] | None:
    """Require the existing Telegram channel to approve first-time WhatsApp access."""
    channel_user_id = message.channel_user_id
    if message.channel != WHATSAPP_CHANNEL or not looks_like_phone_number(channel_user_id):
        return None

    if not hasattr(user_repository, "get_channel_identity_by_phone"):
        return None

    phone_number = str(getattr(user, "phone_number", "") or "").strip()
    authorizing_identity = await user_repository.get_channel_identity_by_phone(phone_number, TELEGRAM_CHANNEL)
    if not authorizing_identity:
        return None

    existing_whatsapp_user = await user_repository.get_by_channel_identity(WHATSAPP_CHANNEL, channel_user_id)
    if existing_whatsapp_user:
        if str(getattr(existing_whatsapp_user, "id", "")) != str(getattr(user, "id", "")):
            logger.warning(
                "channel_link_identity_conflict",
                requested_channel=WHATSAPP_CHANNEL,
                phone_number=phone_number,
            )
            await enqueue_outbox_say(
                publisher,
                channel_user_id,
                WHATSAPP_CHANNEL,
                "This WhatsApp number is linked to another profile. Please contact support.",
                metadata={"source": "channel_link_guard", "reason": "identity_conflict"},
            )
            return {"status": "channel_link_identity_conflict"}
        return None

    flow_token = _new_channel_link_token()
    stored = await session_manager.update_session_strict(
        flow_token,
        {
            "purpose": CHANNEL_LINK_SESSION_PURPOSE,
            "user_id": str(getattr(user, "id", "")),
            "phone_number": phone_number,
            "requested_channel": WHATSAPP_CHANNEL,
            "requested_channel_user_id": channel_user_id,
            "requested_channel_actor_id": channel_user_id,
            "authorizing_channel": TELEGRAM_CHANNEL,
            "authorizing_channel_user_id": str(authorizing_identity),
            "step": "pending_existing_channel_authorization",
        },
        verify=True,
    )
    if not stored:
        logger.error("channel_link_session_store_failed", requested_channel=WHATSAPP_CHANNEL)
        await enqueue_outbox_say(
            publisher,
            channel_user_id,
            WHATSAPP_CHANNEL,
            "I couldn't start that link request. Please try again.",
            metadata={"source": "channel_link_guard", "reason": "session_store_failed"},
        )
        return {"status": "channel_link_session_store_failed"}

    try:
        logger.info(
            "whatsapp_identity_link_telegram_pin_flow_send",
            flow_token_hash=log_fingerprint(flow_token),
            pin_flow_token_hash=log_fingerprint(build_channel_link_pin_token(flow_token)),
            authorizing_identity_hash=log_fingerprint(str(authorizing_identity)),
            requested_whatsapp_hash=log_fingerprint(channel_user_id),
        )
        result = await telegram_client_factory().send_flow(
            to=str(authorizing_identity),
            flow_id="pin_entry",
            flow_config={
                "header": "Authorize WhatsApp link",
                "text_body": (
                    "Enter your transaction PIN to link WhatsApp to your banking profile. "
                    "Continue only if this request was from you."
                ),
                "flow_cta": "Enter PIN",
                "flow_token": build_channel_link_pin_token(flow_token),
            },
            suppress_typing_indicator=True,
        )
        logger.info(
            "whatsapp_identity_link_telegram_pin_flow_sent",
            flow_token_hash=log_fingerprint(flow_token),
            message_id=getattr(result, "message_id", None),
            requested_whatsapp_hash=log_fingerprint(channel_user_id),
        )
    except Exception as e:
        logger.error("channel_link_authorization_send_failed", requested_channel=WHATSAPP_CHANNEL, error=str(e))
        await session_manager.delete_session(flow_token)
        await enqueue_outbox_say(
            publisher,
            channel_user_id,
            WHATSAPP_CHANNEL,
            "I couldn't send the Telegram approval request. Please try again.",
            metadata={"source": "channel_link_guard", "reason": "authorization_send_failed"},
        )
        return {"status": "channel_link_authorization_send_failed"}

    if not result.success:
        logger.error(
            "channel_link_authorization_send_failed",
            requested_channel=WHATSAPP_CHANNEL,
            error=result.error,
        )
        await session_manager.delete_session(flow_token)
        await enqueue_outbox_say(
            publisher,
            channel_user_id,
            WHATSAPP_CHANNEL,
            "I couldn't send the Telegram approval request. Please try again.",
            metadata={"source": "channel_link_guard", "reason": "authorization_send_failed"},
        )
        return {"status": "channel_link_authorization_send_failed"}

    await enqueue_outbox_say(
        publisher,
        channel_user_id,
        WHATSAPP_CHANNEL,
        (
            "I sent a secure PIN request to your existing Telegram channel. "
            "Enter your PIN there to finish linking WhatsApp."
        ),
        metadata={"source": "channel_link_guard", "reason": "authorization_pending"},
    )
    logger.info(
        "channel_link_authorization_requested",
        requested_channel=WHATSAPP_CHANNEL,
        authorizing_channel=TELEGRAM_CHANNEL,
    )
    return {
        "status": "channel_link_authorization_pending",
        "authorizing_channel": TELEGRAM_CHANNEL,
    }
