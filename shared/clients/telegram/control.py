"""Telegram bot control endpoint helpers."""

from collections.abc import Awaitable, Callable
from typing import Any

from shared.clients.telegram.payloads import (
    build_answer_callback_query_payload,
    build_authorized_reply_markup_payload,
    build_remove_inline_keyboard_payload,
    build_typing_indicator_payload,
)
from shared.utils.logging import get_logger, log_fingerprint

ApiCall = Callable[..., Awaitable[dict[str, Any]]]

logger = get_logger(__name__)


async def send_typing_indicator(*, api_call: ApiCall, chat_id: str) -> bool:
    """Send typing indicator (chat action)."""
    try:
        await api_call(
            "sendChatAction",
            build_typing_indicator_payload(chat_id=chat_id),
            max_retries=1,
        )
        return True
    except Exception as e:
        logger.warning(
            "telegram_typing_indicator_failed",
            chat_id_hash=log_fingerprint(chat_id),
            error_type=type(e).__name__,
        )
        return False


async def answer_callback_query(
    *,
    api_call: ApiCall,
    callback_query_id: str,
    text: str,
) -> bool:
    """Answer a callback query (acknowledge inline button press)."""
    try:
        await api_call(
            "answerCallbackQuery",
            build_answer_callback_query_payload(callback_query_id=callback_query_id, text=text),
            max_retries=1,
        )
        return True
    except Exception as e:
        logger.warning(
            "telegram_answer_callback_failed",
            callback_query_id_hash=log_fingerprint(callback_query_id),
            error_type=type(e).__name__,
        )
        return False


async def mark_as_authorized(*, api_call: ApiCall, chat_id: str, message_id: str | int) -> bool:
    """Replace the PIN Web App button with a non-interactive authorized badge."""
    try:
        await api_call(
            "editMessageReplyMarkup",
            build_authorized_reply_markup_payload(chat_id=chat_id, message_id=message_id),
            max_retries=1,
        )
        return True
    except Exception as e:
        logger.warning(
            "telegram_mark_authorized_failed",
            chat_id_hash=log_fingerprint(chat_id),
            message_id_hash=log_fingerprint(message_id),
            error_type=type(e).__name__,
        )
        return False


async def remove_inline_keyboard(*, api_call: ApiCall, chat_id: str, message_id: str | int) -> bool:
    """Remove an inline keyboard from a sent Telegram message."""
    try:
        await api_call(
            "editMessageReplyMarkup",
            build_remove_inline_keyboard_payload(chat_id=chat_id, message_id=message_id),
            max_retries=1,
        )
        return True
    except Exception as e:
        logger.warning(
            "telegram_remove_inline_keyboard_failed",
            chat_id_hash=log_fingerprint(chat_id),
            message_id_hash=log_fingerprint(message_id),
            error_type=type(e).__name__,
        )
        return False


async def set_webhook(*, api_call: ApiCall, webhook_url: str) -> bool:
    """Register the webhook URL with Telegram."""
    try:
        result = await api_call("setWebhook", {"url": webhook_url})
        logger.info("telegram_webhook_set", webhook_url_hash=log_fingerprint(webhook_url))
        return result.get("ok", False)
    except Exception as e:
        logger.error("telegram_webhook_set_failed", error_type=type(e).__name__)
        return False


async def set_my_commands(*, api_call: ApiCall, commands: list[dict[str, str]]) -> bool:
    """Register persistent bot menu commands."""
    try:
        result = await api_call("setMyCommands", {"commands": commands})
        logger.info("telegram_bot_commands_updated", command_count=len(commands))
        return result.get("ok", False)
    except Exception as e:
        logger.error("telegram_bot_commands_update_failed", error_type=type(e).__name__)
        return False
