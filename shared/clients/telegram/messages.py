"""Telegram text and interactive message send flows."""

from collections.abc import Awaitable, Callable
from typing import Any

from shared.clients.abstractions.messaging import MessageResult
from shared.clients.telegram.payloads import build_interactive_message_payload, build_send_message_payload
from shared.utils.logging import get_logger, log_fingerprint

ApiCall = Callable[..., Awaitable[dict[str, Any]]]

logger = get_logger(__name__)


def _message_result_from_response(result: dict[str, Any]) -> MessageResult:
    msg_data = result.get("result", {})
    sent_id = str(msg_data.get("message_id", ""))
    return MessageResult(success=True, message_id=sent_id, raw_response=result)


async def send_text(
    *,
    api_call: ApiCall,
    to: str,
    text: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
) -> MessageResult:
    """Send a plain text message via Telegram."""
    del suppress_typing_indicator
    payload = build_send_message_payload(to=to, text=text, message_id=message_id)

    try:
        result = await api_call("sendMessage", payload)
        return _message_result_from_response(result)
    except Exception as e:
        logger.error("telegram_send_text_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        return MessageResult(success=False, error="Telegram send failed")


async def send_interactive(
    *,
    api_call: ApiCall,
    to: str,
    body_text: str,
    options: list[dict[str, str]],
    header: str,
    footer: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
) -> MessageResult:
    """Send an interactive message with inline keyboard buttons."""
    del message_id, suppress_typing_indicator
    payload = build_interactive_message_payload(
        to=to,
        body_text=body_text,
        options=options,
        header=header,
        footer=footer,
    )

    try:
        result = await api_call("sendMessage", payload)
        return _message_result_from_response(result)
    except Exception as e:
        logger.error("telegram_send_interactive_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        return MessageResult(success=False, error="Telegram send failed")
