"""WhatsApp Flow handler for PIN-authorized channel linking."""

from typing import Any

from fastapi.responses import Response

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import (
    format_error_response,
    format_success_response,
)
from shared.clients.telegram.client import TelegramClient
from shared.services.channel_linking import complete_channel_link_with_pin
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_channel_link_pin(
    data: dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    authorizing_channel_user_id: str | None = None,
) -> Response:
    """Verify PIN from WhatsApp Flow and complete a pending channel link."""
    pin = data.get("pin")
    if not pin:
        return format_error_response(
            "Pin",
            "PIN is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    result = await complete_channel_link_with_pin(
        flow_token=flow_token,
        pin=str(pin),
        authorizing_channel="whatsapp",
        authorizing_channel_user_id=authorizing_channel_user_id,
    )
    if not result.success:
        return format_error_response(
            "Pin",
            result.error or "PIN verification failed",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            attempts_remaining=result.attempts_remaining,
            locked=result.locked,
        )

    if result.requested_channel == "telegram":
        try:
            await TelegramClient().send_text(
                to=result.requested_channel_user_id,
                text="Your Telegram account has been linked. You can now use banking features here.",
            )
        except Exception as e:
            logger.warning("channel_link_requested_channel_notify_failed", channel="telegram", error=str(e))

    return format_success_response(
        "SUCCESS",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        extension_message_response={
            "params": {
                "flow_token": flow_token or "completed",
                "success": True,
            }
        },
    )
