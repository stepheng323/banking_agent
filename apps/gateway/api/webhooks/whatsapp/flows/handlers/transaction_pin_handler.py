"""Unified PIN handler for all transaction types (transfer, airtime, data).

This handler:
1. Validates and verifies PIN using shared.services.auth
2. Publishes a FlowEvent to the queue for core to handle
3. Returns success/error response to WhatsApp Flow
"""

import asyncio
from typing import Any

from fastapi.responses import Response

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import (
    format_error_response,
    format_success_response,
)
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.adapter import QueuePublisher
from shared.queue.factory import QueuePublisherFactory
from shared.queue.messages import FlowEvent, FlowEventType
from shared.services.auth import AuthorizationService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_transaction_pin(
    data: dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
    publisher: QueuePublisher | None = None,
) -> Response:
    """
    Unified PIN handler for all transaction types.

    Validates PIN, verifies against user's stored PIN, and publishes
    a FlowEvent for core to handle the resume.

    Args:
        data: Flow data containing PIN
        flow_token: Flow token (format: {type}-pin-{idempotency_key}-{phone_number})
        request_was_encrypted: Whether request was encrypted
        aes_key_bytes: AES key for encryption
        iv_bytes: IV for encryption
        whatsapp_client: WhatsApp client instance
    """
    pin = data.get("pin")

    logger.info("transaction_pin_received", has_flow_token=bool(flow_token))

    if not pin:
        return format_error_response(
            "Pin",
            "PIN is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    transaction_type = None
    idem_key = None
    if flow_token and flow_token.startswith("transaction-pin-"):
        parts = flow_token.split("-")
        idem_key = "-".join(parts[2:-1])
    elif flow_token:
        for prefix in ("transfer", "airtime", "data"):
            token_prefix = f"{prefix}-pin-"
            if flow_token.startswith(token_prefix):
                parts = flow_token.split("-")
                idem_key = "-".join(parts[2:-1])
                transaction_type = prefix
                break

        if idem_key is None:
            return format_success_response(
                "SUCCESS",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
                extension_message_response={
                    "params": {
                        "flow_token": flow_token or "completed",
                        "pin": str(pin),
                        "success": True,
                    }
                },
            )
    else:
        return format_success_response(
            "SUCCESS",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            extension_message_response={
                "params": {
                    "flow_token": "completed",
                    "pin": str(pin),
                    "success": True,
                }
            },
        )

    redis_client = RedisClient.get_client()

    phone_number = await redis_client.get(f"transaction:token:{idem_key}:phone")
    if not phone_number:
        phone_number = await redis_client.get(f"transfer:token:{idem_key}:phone")
    if not phone_number:
        phone_number = await redis_client.get(f"airtime:token:{idem_key}:phone")
    if not phone_number:
        phone_number = await redis_client.get(f"data:token:{idem_key}:phone")

    if not phone_number:
        phone_number = flow_token.split("-")[-1] if flow_token else None

        if phone_number:
            if publisher is None:
                publisher = QueuePublisherFactory.get_publisher()

            asyncio.create_task(
                publisher.publish(
                    topic="notification.send",
                    message={
                        "phone_number": phone_number,
                        "channel": whatsapp_client.channel_name,
                        "intents": [
                            {
                                "type": "say",
                                "text": "Your transaction session has expired. Please start a new transaction.",
                            }
                        ],
                        "metadata": {"source": "transaction_pin_handler", "status": "expired"},
                    },
                )
            )

        return format_success_response(
            "SUCCESS",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            extension_message_response={
                "params": {
                    "flow_token": "expired",
                }
            },
        )

    authorization_service = AuthorizationService(redis_client=redis_client)

    auth_result = await authorization_service.verify_pin(
        phone_number, str(pin), idem_key, transaction_type=transaction_type
    )

    if not transaction_type:
        transaction_type = auth_result.transaction_type

    if not transaction_type:
        return format_error_response(
            "Pin",
            "Unable to determine transaction type. Please start a new transaction.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    await authorization_service.store_pin_verification_result(idem_key, auth_result)

    if not auth_result.verified:
        error_msg = auth_result.error or "PIN verification failed"
        return format_error_response(
            "Pin",
            error_msg,
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    flow_event = FlowEvent(
        event_type=FlowEventType.PIN_VERIFIED,
        phone_number=phone_number,
        flow_type=transaction_type,
        idempotency_key=idem_key,
        success=True,
    )

    try:
        if publisher is None:
            publisher = QueuePublisherFactory.get_publisher()

        await publisher.publish(
            "flow_event.process",
            message={
                "event_type": flow_event.event_type.value,
                "phone_number": flow_event.phone_number,
                "flow_type": flow_event.flow_type,
                "idempotency_key": flow_event.idempotency_key,
                "success": flow_event.success,
                "error": flow_event.error,
                "extra_data": flow_event.extra_data,
                "channel": whatsapp_client.channel_name,
            },
        )
    except Exception as e:
        logger.error("flow_event_publish_failed", error=str(e), exc_info=True)
        return format_error_response(
            "Pin",
            f"Failed to process {transaction_type} authorization. Please try again.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    return format_success_response(
        "SUCCESS",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        extension_message_response={
            "params": {
                "flow_token": flow_token or "completed",
                "pin": str(pin),
                "success": True,
            }
        },
    )
