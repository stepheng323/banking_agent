"""WhatsApp media message send flows."""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

import shared.clients.whatsapp.media as whatsapp_media
import shared.clients.whatsapp.typing as whatsapp_typing
from shared.clients.whatsapp.endpoints import message_url
from shared.clients.whatsapp.payloads import build_document_message_payload, build_image_message_payload
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

SendRequest = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def send_image_from_url(
    *,
    to: str,
    image_url: str,
    caption: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
    http_client: httpx.AsyncClient,
    phone_number_id: str,
    headers: dict[str, str],
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> dict[str, Any]:
    await whatsapp_typing.maybe_send_typing_indicator(
        to=to,
        message_id=message_id,
        suppress_typing_indicator=suppress_typing_indicator,
        send_typing_indicator=send_typing_indicator,
    )
    media_id = await whatsapp_media.upload_media_from_url(
        http_client=http_client,
        phone_number_id=phone_number_id,
        headers=headers,
        media_url=image_url,
    )
    payload = build_image_message_payload(to=to, media_id=media_id, caption=caption)
    result = await send(message_url(phone_number_id), payload)
    logger.info("whatsapp_image_sent", to_hash=log_fingerprint(to), media_id_hash=log_fingerprint(media_id))
    return result


async def send_image_data(
    *,
    to: str,
    data: bytes,
    caption: str,
    mime_type: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
    http_client: httpx.AsyncClient,
    access_token: str,
    phone_number_id: str,
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> dict[str, Any]:
    await whatsapp_typing.maybe_send_typing_indicator(
        to=to,
        message_id=message_id,
        suppress_typing_indicator=suppress_typing_indicator,
        send_typing_indicator=send_typing_indicator,
    )
    extension = mime_type.split("/")[-1]
    media_id = await whatsapp_media.upload_buffer(
        http_client=http_client,
        access_token=access_token,
        phone_number_id=phone_number_id,
        data=data,
        filename=f"image.{extension}",
        mime_type=mime_type,
    )
    payload = build_image_message_payload(to=to, media_id=media_id, caption=caption)
    result = await send(message_url(phone_number_id), payload)
    logger.info(
        "whatsapp_image_data_sent",
        to_hash=log_fingerprint(to),
        media_id_hash=log_fingerprint(media_id),
    )
    return result


async def send_document(
    *,
    to: str,
    data: bytes,
    filename: str,
    caption: str,
    mime_type: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
    http_client: httpx.AsyncClient,
    access_token: str,
    phone_number_id: str,
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> dict[str, Any]:
    await whatsapp_typing.maybe_send_typing_indicator(
        to=to,
        message_id=message_id,
        suppress_typing_indicator=suppress_typing_indicator,
        send_typing_indicator=send_typing_indicator,
    )
    media_id = await whatsapp_media.upload_buffer(
        http_client=http_client,
        access_token=access_token,
        phone_number_id=phone_number_id,
        data=data,
        filename=filename,
        mime_type=mime_type,
    )
    payload = build_document_message_payload(to=to, media_id=media_id, filename=filename, caption=caption)
    result = await send(message_url(phone_number_id), payload)
    logger.info(
        "whatsapp_document_sent",
        to_hash=log_fingerprint(to),
        filename_hash=log_fingerprint(filename),
        media_id_hash=log_fingerprint(media_id),
    )
    return result
