"""Unified PIN handler for all transaction types (transfer, airtime, data, batch).

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
from apps.gateway.core.config import settings
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.messages import FlowEvent, FlowEventType
from shared.queue.redis_queue import RedisQueue
from shared.services.auth import AuthorizationService


async def handle_transaction_pin(
    data: dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
    redis_queue: RedisQueue,
) -> Response:
    """
    Unified PIN handler for all transaction types.

    Validates PIN, verifies against user's stored PIN, and publishes
    a FlowEvent for core to handle the resume.

    Args:
        data: Flow data containing PIN
        flow_token: Flow token (format: transaction-pin-{idempotency_key})
        request_was_encrypted: Whether request was encrypted
        aes_key_bytes: AES key for encryption
        iv_bytes: IV for encryption
        whatsapp_client: WhatsApp client instance
        redis_queue: RedisQueue for publishing events
    """
    pin = data.get("pin")

    if not pin:
        return format_error_response(
            "Pin",
            "PIN is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    if not flow_token or not flow_token.startswith("transaction-pin-"):
        if flow_token and flow_token.startswith("batch-auth-"):
            transaction_type = "batch"
            idem_key = flow_token
        elif flow_token and flow_token.startswith("transfer-pin-"):
            parts = flow_token.split("-")
            idem_key = "-".join(parts[2:-1])
            transaction_type = "transfer"
        elif flow_token and flow_token.startswith("airtime-pin-"):
            parts = flow_token.split("-")
            idem_key = "-".join(parts[2:-1])
            transaction_type = "airtime"
        else:
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
        parts = flow_token.split("-")
        idem_key = "-".join(parts[2:-1])
        transaction_type = None

    redis_client = RedisClient.get_client()

    if transaction_type == "batch":
        parts = flow_token.split("-")
        phone_number = parts[2] if len(parts) >= 3 else None
    else:
        # DEBUG PROBE
        print(f"DEBUG: Processing flow_token: {flow_token}", flush=True)
        print(f"DEBUG: Extracted idem_key: {idem_key}", flush=True)

        last_token = await redis_client.get("debug:last_set_token")
        print(f"DEBUG: Last Worker Token: {last_token}", flush=True)

        if last_token:
            debug_phone = await redis_client.get(f"debug:token:{last_token}")
            print(f"DEBUG: Phone for Last Token: {debug_phone}", flush=True)

        phone_number = await redis_client.get(f"transaction:token:{idem_key}:phone")
        print(f"DEBUG: Lookup transaction:token -> {phone_number}", flush=True)

        if not phone_number:
            phone_number = await redis_client.get(f"transfer:token:{idem_key}:phone")
            print(f"DEBUG: Lookup transfer:token -> {phone_number}", flush=True)

            if not phone_number:
                phone_number = await redis_client.get(f"airtime:token:{idem_key}:phone")
                print(f"DEBUG: Lookup airtime:token -> {phone_number}", flush=True)

    if not phone_number:
        phone_number = flow_token.split("-")[-1] if flow_token else None

        if phone_number:
            asyncio.create_task(
                whatsapp_client.send_text(
                    to=phone_number,
                    text="Your transaction session has expired. Please start a new transaction.",
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

    try:
        if redis_queue is None:
            redis_queue = RedisQueue(redis_url=settings.redis_url)

        flow_event = FlowEvent(
            event_type=FlowEventType.PIN_VERIFIED,
            phone_number=phone_number,
            flow_type=transaction_type,
            idempotency_key=idem_key,
            success=True,
        )
        await redis_queue.publish_flow_event(flow_event)
    except Exception as e:
        print(f"Error publishing flow event: {e}")
        import traceback

        traceback.print_exc()
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
