"""WhatsApp text and interactive message send flows."""

from collections.abc import Awaitable, Callable
from typing import Any

import shared.clients.whatsapp.typing as whatsapp_typing
from shared.clients.abstractions.messaging import MessageResult
from shared.clients.whatsapp.payloads import (
    build_button_message_payload,
    build_list_message_payload,
    build_text_message_payload,
)
from shared.utils.logging import get_logger, log_fingerprint

SendRequest = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

logger = get_logger(__name__)


async def send_text(
    *,
    url: str,
    to: str,
    text: str,
    preview_url: bool,
    message_id: str | None,
    suppress_typing_indicator: bool,
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> dict[str, Any]:
    """Send a text message to a WhatsApp number."""
    await whatsapp_typing.maybe_send_typing_indicator(
        to=to,
        message_id=message_id,
        suppress_typing_indicator=suppress_typing_indicator,
        send_typing_indicator=send_typing_indicator,
    )
    payload = build_text_message_payload(to=to, text=text, preview_url=preview_url)

    try:
        return await send(url, payload)
    except Exception as e:
        logger.error("whatsapp_send_text_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        raise


async def send_button(
    *,
    url: str,
    to: str,
    body_text: str,
    buttons: list[dict[str, str]],
    header: str,
    footer: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> dict[str, Any]:
    """Send an interactive button message."""
    await whatsapp_typing.maybe_send_typing_indicator(
        to=to,
        message_id=message_id,
        suppress_typing_indicator=suppress_typing_indicator,
        send_typing_indicator=send_typing_indicator,
    )
    payload = build_button_message_payload(
        to=to,
        body_text=body_text,
        buttons=buttons,
        header=header,
        footer=footer,
    )

    try:
        result = await send(url, payload)
        logger.info("whatsapp_button_sent", to_hash=log_fingerprint(to))
        return result
    except Exception as e:
        logger.error("whatsapp_button_send_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        raise


async def send_list(
    *,
    url: str,
    to: str,
    body_text: str,
    options: list[dict[str, str]],
    header: str,
    footer: str,
    list_button_text: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> dict[str, Any]:
    """Send an interactive list message."""
    if not options:
        raise ValueError("List options cannot be empty")

    await whatsapp_typing.maybe_send_typing_indicator(
        to=to,
        message_id=message_id,
        suppress_typing_indicator=suppress_typing_indicator,
        send_typing_indicator=send_typing_indicator,
    )
    payload = build_list_message_payload(
        to=to,
        body_text=body_text,
        options=options,
        header=header,
        footer=footer,
        list_button_text=list_button_text,
    )

    try:
        result = await send(url, payload)
        logger.info("whatsapp_list_sent", to_hash=log_fingerprint(to))
        return result
    except Exception as e:
        logger.error("whatsapp_list_send_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        raise


async def send_interactive(
    *,
    url: str,
    to: str,
    body_text: str,
    options: list[dict[str, str]],
    header: str,
    footer: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> MessageResult:
    """Send WhatsApp native buttons/lists and return MessagingClient result shape."""
    try:
        if len(options) <= 3:
            result = await send_button(
                url=url,
                to=to,
                body_text=body_text,
                buttons=options,
                header=header,
                footer=footer,
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
                send=send,
                send_typing_indicator=send_typing_indicator,
            )
        elif len(options) <= 10:
            result = await send_list(
                url=url,
                to=to,
                body_text=body_text,
                options=options,
                header=header,
                footer=footer,
                list_button_text="View options",
                message_id=message_id,
                suppress_typing_indicator=suppress_typing_indicator,
                send=send,
                send_typing_indicator=send_typing_indicator,
            )
        else:
            raise ValueError("WhatsApp interactive supports at most 10 options")
        msg_id = result.get("messages", [{}])[0].get("id")
        return MessageResult(success=True, message_id=msg_id, raw_response=result)
    except Exception as e:
        return MessageResult(success=False, error=str(e))
