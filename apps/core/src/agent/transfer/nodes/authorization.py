"""Authorization node for transfer flow."""

import json
from typing import cast

from apps.core.src.agent.transfer.state import TransferState
from apps.gateway.api.flows.transaction_service import create_transfer_transaction
from apps.core.src.agent.services.authorization_service import AuthorizationService
from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue


async def authorize_transaction(
    state: TransferState,
    redis_client: Redis,
    queue: RedisQueue,
) -> TransferState:
    """Authorize transaction after PIN verification."""
    phone_number = state.get("phone_number")
    idem_key = state.get("idempotency_key")

    if not idem_key:
        return cast(
            TransferState,
            {
                **state,
                "response": "Missing transaction identifier. Please start a new transfer.",
                "flow_state": "error",
                "transfer_status": "failed",
            },
        )

    authorization_service = AuthorizationService(redis_client=redis_client)
    pin_result = await authorization_service.get_pin_verification_result(idem_key)
    pin_verified_in_state = state.get("pin_verified")

    # If no pin_result but pin_verified is True in state, proceed (PIN was verified in handler)
    if not pin_result:
        if pin_verified_in_state is True:
            # We'll get user_id from pending_transfer data below
            pass
        else:
            return cast(
                TransferState,
                {
                    **state,
                    "response": "PIN verification pending. Please enter your PIN.",
                    "flow_state": "authorizing",
                },
            )
    elif not pin_result.verified:
        error_msg = pin_result.error or "PIN verification failed"
        retry_count = pin_result.retry_count

        if retry_count >= 3:
            await redis_client.delete(f"user:{phone_number}:pending_transfer")
            return cast(
                TransferState,
                {
                    **state,
                    "response": "Maximum PIN attempts exceeded. Please start a new transfer.",
                    "flow_state": "error",
                    "transfer_status": "failed",
                    "pin_verified": False,
                    "pin_verification_error": error_msg,
                    "pin_retry_count": retry_count,
                },
            )

        return cast(
            TransferState,
            {
                **state,
                "response": error_msg,
                "flow_state": "confirming",
                "pin_verified": False,
                "pin_verification_error": error_msg,
                "pin_retry_count": retry_count,
            },
        )

    # PIN is verified (either from pin_result or from state)
    try:
        # OPTIMIZED: Get pending transfer data directly from state (checkpoint)
        # The confirmation node now stores data in the checkpoint, not Redis
        amount = state.get("amount")
        recipient_account = state.get("recipient_account")
        recipient_bank_name = state.get("recipient_bank_name")
        recipient_name = state.get("recipient_name")
        
        if not amount or not recipient_account:
            debug_log(f"❌ Missing transfer data in state for authorization")
            return {
                **state,
                "response": "Session expired or invalid. Please start a new transfer.",
                "transfer_status": "failed",
                "flow_state": "completed",
            }

        pending_transfer = {
            "amount": amount,
            "recipient": {
                "account_number": recipient_account,
                "bank_name": recipient_bank_name,
                "name": recipient_name
            },
            "idempotency_key": state.get("idempotency_key")
        }

        # Get user_id from pin_result or from pending_transfer/user_profile
        user_id = None
        if pin_result and pin_result.user_id:
            user_id = pin_result.user_id
        else:
            # Try to get user_id from user_profile in state
            user_profile = state.get("user_profile")
            if isinstance(user_profile, dict):
                user_id = user_profile.get("id")
            if not user_id:
                return cast(
                    TransferState,
                    {
                        **state,
                        "response": "User information not available. Please try again.",
                        "flow_state": "error",
                        "transfer_status": "failed",
                    },
                )

        transaction_id = await create_transfer_transaction(
            pending_transfer,
            user_id,
            idem_key,
        )

        transfer_request = {
            "type": "execute_transfer",
            "phone_number": phone_number,
            "idempotency_key": idem_key,
            "transfer_data": pending_transfer,
            "transaction_id": transaction_id,
        }

        await queue.enqueue_simple(
            queue_name="banking:transactions",
            message=transfer_request,
        )

        # Clear previous values from Redis so next transfer is treated as new, not a change
        prev_values_key = f"transfer:prev:session:{phone_number}"
        await redis_client.delete(prev_values_key)

        pin_verification_key = f"transaction:pin_verified:{idem_key}"
        await redis_client.delete(pin_verification_key)

        retry_count = pin_result.retry_count if pin_result else 0
        response_message = "Transfer authorized. Processing your request..."
        result = cast(
            TransferState,
            {
                **state,
                "response": response_message,
                "flow_state": "completed",
                "transfer_status": "authorized",
                "pin_verified": True,
                "pin_retry_count": retry_count,
            },
        )
        return result

    except Exception as e:
        error_msg = "Failed to process authorization. Please try again."
        import traceback
        traceback.print_exc()
        return cast(
            TransferState,
            {
                **state,
                "response": error_msg,
                "flow_state": "error",
                "transfer_status": "failed",
            },
        )

