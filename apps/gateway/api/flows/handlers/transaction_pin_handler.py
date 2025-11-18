"""Unified PIN handler for all transaction types (transfer, airtime, data)."""

import json
import traceback
from typing import Any, Dict, Optional

from fastapi.responses import Response

from shared.cache.redis_client import RedisClient
from shared.queue.redis_queue import RedisQueue
from apps.core.src.agent.services.authorization_service import AuthorizationService
from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from apps.gateway.api.flows.transaction_service import create_transfer_transaction

# Import services - these will be injected or created as needed
try:
    from apps.core.src.agent.transfer.service import TransferService
    from apps.core.src.agent.airtime.service import AirtimeService
except ImportError:
    TransferService = None
    AirtimeService = None


async def handle_transaction_pin(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    queue: RedisQueue,
    transfer_service: Optional[TransferService] = None,
    airtime_service: Optional[AirtimeService] = None,
) -> Response:
    """
    Unified PIN handler for all transaction types.

    Validates PIN, verifies against user's stored PIN, stores result in Redis,
    and resumes appropriate graph execution.

    Args:
        data: Flow data containing PIN
        flow_token: Flow token (format: transaction-pin-{idempotency_key})
        request_was_encrypted: Whether request was encrypted
        aes_key_bytes: AES key for encryption
        iv_bytes: IV for encryption
        queue: Redis queue instance
        transfer_service: Optional TransferService instance
        airtime_service: Optional AirtimeService instance
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
        if flow_token and flow_token.startswith("transfer-pin-"):
            idem_key = flow_token.replace("transfer-pin-", "")
            transaction_type = "transfer"
        elif flow_token and flow_token.startswith("airtime-pin-"):
            idem_key = flow_token.replace("airtime-pin-", "")
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
        idem_key = flow_token.replace("transaction-pin-", "")
        transaction_type = None  

    redis_client = RedisClient.get_client()
    phone_number = await redis_client.get(f"transaction:token:{idem_key}:phone")

    if not phone_number:
        phone_number = await redis_client.get(f"transfer:token:{idem_key}:phone")
        if not phone_number:
            phone_number = await redis_client.get(f"airtime:token:{idem_key}:phone")

    if not phone_number:
        print(f"⚠️  No phone number found for idem_key: {idem_key}")
        return format_error_response(
            "Pin",
            "Transaction session expired. Please start a new transaction.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    authorization_service = AuthorizationService(redis_client=redis_client)

    auth_result = await authorization_service.verify_pin(phone_number, str(pin), idem_key)

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
        print(f"❌ PIN verification failed for {transaction_type}: {idem_key} - {error_msg}")
        return format_error_response(
            "Pin",
            error_msg,
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    print(f"✅ PIN verified for {transaction_type}: {idem_key} (user_id={auth_result.user_id})")

    try:
        if transaction_type == "transfer":
            if transfer_service and hasattr(transfer_service.graph, "resume_after_pin_verification"):
                response_message = await transfer_service.graph.resume_after_pin_verification(
                    phone_number, True, None
                )
            else:
                pending_data = await redis_client.get(f"user:{phone_number}:pending_transfer")
                if not pending_data:
                    return format_error_response(
                        "Pin",
                        "Transfer session expired. Please start a new transfer.",
                        request_was_encrypted,
                        aes_key_bytes,
                        iv_bytes,
                    )
                pending_transfer = json.loads(pending_data)
                transaction_id = await create_transfer_transaction(
                    pending_transfer,
                    auth_result.user_id,
                    idem_key,
                )
                transfer_request = {
                    "type": "execute_transfer",
                    "phone_number": phone_number,
                    "idempotency_key": idem_key,
                    "transfer_data": pending_transfer,
                    "flow_token": flow_token,
                    "transaction_id": transaction_id,
                }
                await queue.enqueue_simple(
                    queue_name="banking:transfers",
                    message=transfer_request,
                )
                response_message = "Transfer authorized. Processing your request..."

        elif transaction_type == "airtime":
            if airtime_service and hasattr(airtime_service.graph, "resume_after_pin_verification"):
                response_message = await airtime_service.graph.resume_after_pin_verification(
                    phone_number, True, None
                )
            else:
                response_message = "Airtime purchase authorized. Processing your request..."

        else:
            response_message = f"{transaction_type.capitalize()} transaction authorized. Processing your request..."

    except Exception as e:
        print(f"❌ Error resuming graph for {transaction_type}: {e}")
        traceback.print_exc()
        return format_error_response(
            "Pin",
            "Failed to process authorization. Please try again.",
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

