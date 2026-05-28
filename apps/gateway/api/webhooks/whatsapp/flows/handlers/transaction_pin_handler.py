"""Unified PIN handler for all transaction types (transfer, airtime, data).

This handler:
1. Validates and verifies PIN using shared.services.auth
2. Publishes a FlowEvent to the queue for core to handle
3. Returns success/error response to WhatsApp Flow
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi.responses import Response

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import (
    format_error_response,
    format_success_response,
)
from apps.gateway.api.webhooks.whatsapp.flows.session_owner import (
    format_owner_error_response,
    has_required_provider_identity,
    whatsapp_identity_matches,
)
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.adapter import QueuePublisher
from shared.queue.factory import QueuePublisherFactory
from shared.queue.messages import FlowEvent, FlowEventType
from shared.services.auth.authorization import AuthorizationService
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)
_INVALID_SESSION_MESSAGE = "Invalid transaction session. Please start a new transaction."
_PIN_FLOW_PREFIXES = frozenset({"transfer", "airtime", "data", "schedule"})


@dataclass(frozen=True)
class ParsedTransactionPinToken:
    transaction_type: str | None
    idempotency_key: str
    phone_hint: str | None


def parse_transaction_pin_flow_token(flow_token: str | None) -> ParsedTransactionPinToken | None:
    token = str(flow_token or "").strip()
    if "-pin-" not in token:
        return None

    prefix, remainder = token.split("-pin-", 1)
    if prefix not in _PIN_FLOW_PREFIXES or not remainder:
        return None

    idempotency_key = remainder
    phone_hint = None
    if "-" in remainder:
        maybe_idempotency_key, maybe_phone_hint = remainder.rsplit("-", 1)
        if maybe_idempotency_key and maybe_phone_hint:
            idempotency_key = maybe_idempotency_key
            phone_hint = maybe_phone_hint

    return ParsedTransactionPinToken(
        transaction_type=prefix,
        idempotency_key=idempotency_key,
        phone_hint=phone_hint,
    )


async def handle_transaction_pin(
    data: dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
    publisher: QueuePublisher | None = None,
    authorizing_channel_user_id: str | None = None,
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

    parsed_token = parse_transaction_pin_flow_token(flow_token)

    logger.info(
        "transaction_pin_received",
        data_keys=sorted(str(key) for key in data),
        has_flow_token=bool(flow_token),
        flow_token_hash=log_fingerprint(flow_token),
        parsed_token=bool(parsed_token),
        parsed_idempotency_key_hash=log_fingerprint(parsed_token.idempotency_key if parsed_token else None),
        parsed_phone_hint_hash=log_fingerprint(parsed_token.phone_hint if parsed_token else None),
        parsed_transaction_type=parsed_token.transaction_type if parsed_token else None,
        has_authorizing_channel_user_id=bool(authorizing_channel_user_id),
        authorizing_channel_user_id_hash=log_fingerprint(authorizing_channel_user_id),
    )

    if not pin:
        return format_error_response(
            "Pin",
            "PIN is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    if not parsed_token:
        return format_error_response(
            "Pin",
            _INVALID_SESSION_MESSAGE,
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    if not has_required_provider_identity(authorizing_channel_user_id):
        return format_owner_error_response("Pin", request_was_encrypted, aes_key_bytes, iv_bytes)

    transaction_type = parsed_token.transaction_type
    idem_key = parsed_token.idempotency_key

    redis_client = RedisClient.get_client()

    phone_number = await redis_client.get(f"{transaction_type}:token:{idem_key}:phone")

    expected_phone = phone_number or parsed_token.phone_hint
    if (
        authorizing_channel_user_id
        and expected_phone
        and not whatsapp_identity_matches(authorizing_channel_user_id, expected_phone)
    ):
        logger.warning(
            "transaction_pin_authorizer_mismatch",
            idempotency_key_hash=log_fingerprint(idem_key),
            expected_phone_hash=log_fingerprint(expected_phone),
            authorizer_hash=log_fingerprint(authorizing_channel_user_id),
        )
        return format_owner_error_response("Pin", request_was_encrypted, aes_key_bytes, iv_bytes)

    if not phone_number:
        phone_number = parsed_token.phone_hint

        if phone_number:
            if publisher is None:
                publisher = QueuePublisherFactory.get_publisher()

            from shared.services.delivery_service import DeliveryService

            delivery_service = DeliveryService()
            asyncio.create_task(
                delivery_service.deliver_text(
                    phone_number=str(phone_number),
                    channel=whatsapp_client.channel_name,
                    text="Your transaction session has expired. Please start a new transaction.",
                    metadata={"source": "transaction_pin_handler", "status": "expired"},
                    dedupe_key=f"expired-session:{idem_key}",
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
                "success": "true",
            }
        },
    )
