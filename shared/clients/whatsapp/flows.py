"""WhatsApp Flow message send helpers."""

from collections.abc import Awaitable, Callable
from typing import Any

import shared.clients.whatsapp.typing as whatsapp_typing
from shared.clients.abstractions.messaging import MessageResult
from shared.clients.whatsapp.payloads import build_flow_message_payload
from shared.utils.logging import get_logger, log_fingerprint

SendRequest = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

logger = get_logger(__name__)


async def send_flow(
    *,
    url: str,
    to: str,
    flow_id: str,
    flow_config: dict[str, Any],
    message_id: str | None,
    suppress_typing_indicator: bool,
    send: SendRequest,
    send_typing_indicator: whatsapp_typing.SendTypingIndicator,
) -> MessageResult:
    """Send a WhatsApp Flow."""
    await whatsapp_typing.maybe_send_typing_indicator(
        to=to,
        message_id=message_id,
        suppress_typing_indicator=suppress_typing_indicator,
        send_typing_indicator=send_typing_indicator,
    )

    flow_payload = build_flow_message_payload(to=to, flow_id=flow_id, flow_config=flow_config)
    action_payload = flow_payload.parameters.get("flow_action_payload")
    action_payload_data = action_payload.get("data") if isinstance(action_payload, dict) else None
    logger.info(
        "whatsapp_flow_send_prepared",
        to_hash=log_fingerprint(to),
        flow_id_hash=log_fingerprint(flow_id),
        flow_token_hash=log_fingerprint(flow_payload.flow_token),
        flow_action=flow_payload.flow_action,
        screen_name=flow_payload.screen_name,
        has_flow_action_payload="flow_action_payload" in flow_payload.parameters,
        flow_action_payload_keys=sorted(str(key) for key in action_payload) if isinstance(action_payload, dict) else [],
        flow_action_payload_data_keys=(
            sorted(str(key) for key in action_payload_data) if isinstance(action_payload_data, dict) else []
        ),
    )

    try:
        result = await send(url, flow_payload.payload)
        msg_id = result.get("messages", [{}])[0].get("id")
        logger.info(
            "whatsapp_flow_send_succeeded",
            to_hash=log_fingerprint(to),
            flow_id_hash=log_fingerprint(flow_id),
            flow_token_hash=log_fingerprint(flow_payload.flow_token),
            flow_action=flow_payload.flow_action,
            message_id_hash=log_fingerprint(msg_id),
        )
        return MessageResult(success=True, message_id=msg_id, raw_response=result)
    except Exception as e:
        logger.error(
            "whatsapp_flow_send_failed",
            to_hash=log_fingerprint(to),
            flow_id_hash=log_fingerprint(flow_id),
            flow_token_hash=log_fingerprint(flow_payload.flow_token),
            flow_action=flow_payload.flow_action,
            error_type=type(e).__name__,
        )
        return MessageResult(success=False, error=str(e))
