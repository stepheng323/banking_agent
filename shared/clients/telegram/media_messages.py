"""Telegram media message send flows."""

from collections.abc import Awaitable, Callable
from typing import Any

from shared.clients.abstractions.messaging import MessageResult
from shared.clients.telegram.payloads import (
    build_document_upload_payload,
    build_image_payload,
    build_image_upload_payload,
)
from shared.utils.logging import get_logger, log_fingerprint

ApiCall = Callable[..., Awaitable[dict[str, Any]]]

logger = get_logger(__name__)


def _message_result_from_response(result: dict[str, Any]) -> MessageResult:
    msg_data = result.get("result", {})
    sent_id = str(msg_data.get("message_id", ""))
    return MessageResult(success=True, message_id=sent_id, raw_response=result)


async def send_image_from_url(
    *,
    api_call: ApiCall,
    to: str,
    image_url: str,
    caption: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
) -> MessageResult:
    """Send an image by URL."""
    del message_id, suppress_typing_indicator
    payload = build_image_payload(to=to, image_url=image_url, caption=caption)

    try:
        result = await api_call("sendPhoto", payload)
        return _message_result_from_response(result)
    except Exception as e:
        logger.error("telegram_send_image_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        return MessageResult(success=False, error="Telegram send failed")


async def send_image_data(
    *,
    api_call: ApiCall,
    to: str,
    data: bytes,
    caption: str,
    mime_type: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
) -> MessageResult:
    """Send an image from bytes via multipart upload."""
    del message_id, suppress_typing_indicator
    form_data, files_payload = build_image_upload_payload(
        to=to,
        data=data,
        caption=caption,
        mime_type=mime_type,
    )

    try:
        result = await api_call("sendPhoto", payload=form_data, files=files_payload)
        return _message_result_from_response(result)
    except Exception as e:
        logger.error("telegram_send_image_data_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        return MessageResult(success=False, error="Telegram send failed")


async def send_document(
    *,
    api_call: ApiCall,
    to: str,
    data: bytes,
    filename: str,
    caption: str,
    mime_type: str,
    message_id: str | None,
) -> MessageResult:
    """Send a document via multipart upload."""
    del message_id
    form_data, files_payload = build_document_upload_payload(
        to=to,
        data=data,
        filename=filename,
        caption=caption,
        mime_type=mime_type,
    )

    try:
        result = await api_call("sendDocument", payload=form_data, files=files_payload)
        return _message_result_from_response(result)
    except Exception as e:
        logger.error("telegram_send_document_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        return MessageResult(success=False, error="Telegram send failed")
