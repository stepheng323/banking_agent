"""Handler for Pin screen (transfer flow)."""

import json
import traceback
from typing import Any, Dict

from fastapi.responses import Response

from shared.cache.redis_client import RedisClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils import is_valid_pin_format, verify_hash

from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from apps.gateway.api.flows.transaction_service import create_transfer_transaction


async def handle_transfer_pin(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    queue: RedisQueue,
) -> Response:
    """
    Handle Pin screen for transfer flow.
    
    Validates PIN, verifies against user's stored PIN, creates transaction,
    queues transfer, and returns SUCCESS screen.
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

    if not is_valid_pin_format(str(pin)):
        return format_error_response(
            "Pin",
            "Invalid PIN. Enter a 4-digit numeric PIN.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    if not flow_token or not flow_token.startswith("transfer-pin-"):
        # Default PIN handler (for onboarding, etc.)
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

    idem_key = flow_token.replace("transfer-pin-", "")
    redis_client = RedisClient.get_client()
    phone_number = await redis_client.get(f"transfer:token:{idem_key}:phone")

    if not phone_number:
        print(f"⚠️  No phone number found for idem_key: {idem_key}")
        return format_error_response(
            "Pin",
            "Transfer session expired. Please start a new transfer.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    # Get retry count
    retry_key = f"transfer:retry:{idem_key}"
    retry_count = await redis_client.get(retry_key)
    retry_count = int(retry_count) if retry_count else 0

    if retry_count >= 3:
        print(f"❌ Max retries exceeded for transfer: {idem_key}")
        return format_error_response(
            "Pin",
            "Maximum PIN attempts exceeded. Please start a new transfer.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    pending_data = await redis_client.get(f"user:{phone_number}:pending_transfer")
    if not pending_data:
        print(f"⚠️  No pending transfer found for phone: {phone_number} (may have been cancelled)")
        return format_error_response(
            "Pin",
            "This transfer has been cancelled or expired. Please start a new transfer.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    pending_transfer = json.loads(pending_data)

    with UnitOfWork() as uow:
        if not uow.users:
            return format_error_response(
                "Pin",
                "Database error",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

        user = uow.users.get_by_phone(phone_number)
        if not user:
            print(f"⚠️  User not found: {phone_number}")
            return format_error_response(
                "Pin",
                "User not found. Please contact support.",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

        stored_pin_hash = getattr(user, "transaction_pin", None)
        if not stored_pin_hash:
            print(f"⚠️  No transaction PIN set for user: {phone_number}")
            return format_error_response(
                "Pin",
                "Transaction PIN not set. Please set up your PIN first.",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

        pin_valid = verify_hash(str(pin), stored_pin_hash)

        if not pin_valid:
            retry_count += 1
            await redis_client.set(retry_key, str(retry_count), ex=900)

            attempts_remaining = 3 - retry_count
            error_msg = f"Invalid PIN. {attempts_remaining} attempt(s) remaining."
            if attempts_remaining == 0:
                error_msg = "Invalid PIN. Maximum attempts exceeded. Please start a new transfer."

            print(f"❌ PIN verification failed (attempt {retry_count}/3) for user: {phone_number}")
            return format_error_response(
                "Pin",
                error_msg,
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

    print(f"✅ PIN verified for transfer: {idem_key}")

    # Create transaction record before queueing
    try:
        transaction_id = await create_transfer_transaction(
            pending_transfer,
            str(user.id),
            idem_key,
        )
    except Exception as e:
        print(f"❌ Failed to create transaction: {e}")
        traceback.print_exc()
        return format_error_response(
            "Pin",
            "Failed to process transfer. Please try again.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    transfer_request = {
        "type": "execute_transfer",
        "phone_number": phone_number,
        "idempotency_key": idem_key,
        "transfer_data": pending_transfer,
        "flow_token": flow_token,
        "transaction_id": transaction_id,
    }

    try:
        await queue.enqueue_simple(
            queue_name="banking:transfers",
            message=transfer_request,
        )
        print(f"✅ Transfer queued for execution: {idem_key}")
    except Exception as e:
        print(f"❌ Failed to queue transfer: {e}")
        traceback.print_exc()
        return format_error_response(
            "Pin",
            "Failed to process transfer. Please try again.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    # Return success response immediately (transfer will be processed asynchronously)
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

