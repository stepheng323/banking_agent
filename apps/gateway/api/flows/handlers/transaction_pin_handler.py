"""Unified PIN handler for all transaction types (transfer, airtime, data, batch)."""

import asyncio
from typing import Any, Dict, Optional, TYPE_CHECKING

from fastapi.responses import Response

from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp_client import WhatsAppClient
from apps.core.src.agent.services.authorization_service import AuthorizationService
from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response

if TYPE_CHECKING:
    from apps.core.src.agent.transfer.service import TransferService
    from apps.core.src.agent.airtime.service import AirtimeService
else:
    try:
        from apps.core.src.agent.transfer.service import TransferService
        from apps.core.src.agent.airtime.service import AirtimeService
    except ImportError:
        TransferService = None  # type: ignore
        AirtimeService = None  # type: ignore


async def handle_transaction_pin(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
    transfer_service: Optional["TransferService"] = None,
    airtime_service: Optional["AirtimeService"] = None,
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
        whatsapp_client: WhatsApp client instance
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
        if flow_token and flow_token.startswith("batch-auth-"):
            # Batch authorization
            transaction_type = "batch"
            idem_key = flow_token  # Use full token as key
        elif flow_token and flow_token.startswith("transfer-pin-"):
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
    
    # For batch authorization, extract phone from flow token
    if transaction_type == "batch":
        # Flow token format: batch-auth-{phone}-{timestamp}
        parts = flow_token.split("-")
        if len(parts) >= 3:
            phone_number = parts[2]
        else:
            phone_number = None
    else:
        phone_number = await redis_client.get(f"transaction:token:{idem_key}:phone")

        if not phone_number:
            phone_number = await redis_client.get(f"transfer:token:{idem_key}:phone")
            if not phone_number:
                phone_number = await redis_client.get(f"airtime:token:{idem_key}:phone")

    if not phone_number:
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
        return format_error_response(
            "Pin",
            error_msg,
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )


    try:
        response_message = None
        
        # Handle batch authorization
        if transaction_type == "batch":
            from apps.core.src.agent.services.batch_executor import execute_batch
            from apps.core.src.agent.services.task_queue_service import TaskQueueService
            
            # Send immediate acknowledgment
            await whatsapp_client.send_text(
                phone_number,
                "✅ PIN verified. Authorizing transfers..."
            )
            
            # Execute batch in background
            task_queue_service = TaskQueueService(redis_client=redis_client)
            asyncio.create_task(
                execute_batch(
                    phone_number=phone_number,
                    pin_verified=True,
                    whatsapp_client=whatsapp_client,
                    task_queue_service=task_queue_service,
                    transfer_service=transfer_service,
                    airtime_service=airtime_service,
                )
            )
            
            # Return success immediately
            return format_success_response(
                "SUCCESS",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
                extension_message_response={
                    "params": {
                        "flow_token": flow_token,
                        "pin": str(pin),
                        "success": True,
                    }
                },
            )
        
        # Handle single transfer
        if transaction_type == "transfer":

            if transfer_service and hasattr(transfer_service.graph, "resume_after_pin_verification"):
                try:
                    response_message = await transfer_service.graph.resume_after_pin_verification(
                        phone_number, True, None
                    )
                except Exception:
                    import traceback
                    traceback.print_exc()
                    return format_error_response(
                        "Pin",
                        "Failed to process transfer authorization. Please try again.",
                        request_was_encrypted,
                        aes_key_bytes,
                        iv_bytes,
                    )
            else:
                return format_error_response(
                    "Pin",
                    "Transfer service not available. Please start a new transfer.",
                    request_was_encrypted,
                    aes_key_bytes,
                    iv_bytes,
                )

        elif transaction_type == "airtime":
            if not airtime_service:
                return format_error_response(
                    "Pin",
                    "Airtime service not available. Please start a new airtime purchase.",
                    request_was_encrypted,
                    aes_key_bytes,
                    iv_bytes,
                )
            
            if not hasattr(airtime_service, "graph"):
                return format_error_response(
                    "Pin",
                    "Airtime service not properly initialized. Please start a new airtime purchase.",
                    request_was_encrypted,
                    aes_key_bytes,
                    iv_bytes,
                )
            
            if not hasattr(airtime_service.graph, "resume_after_pin_verification"):
                return format_error_response(
                    "Pin",
                    "Airtime service not properly initialized. Please start a new airtime purchase.",
                    request_was_encrypted,
                    aes_key_bytes,
                    iv_bytes,
                )
            
            try:
                response_message = await airtime_service.graph.resume_after_pin_verification(
                    phone_number, True, None
                )
            except Exception as e:
                print(f"Error in resume_after_pin_verification: {e}")
                import traceback
                traceback.print_exc()
                # Don't return error immediately - try to send a default message instead
                response_message = "Airtime purchase authorized. Processing your request..."

        else:
            return format_error_response(
                "Pin",
                f"Unsupported transaction type: {transaction_type}",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

    except Exception as e:
        print(f"Error processing transaction: {e}")
        import traceback
        traceback.print_exc()
        return format_error_response(
            "Pin",
            f"Failed to process {transaction_type} authorization. Please try again.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    # Ensure we always have a response message
    if not response_message or not response_message.strip():
        response_message = f"{transaction_type.capitalize()} transaction authorized. Processing your request..."

    # Send response message to user via WhatsApp
    if response_message and response_message.strip():
        try:
            await whatsapp_client.send_text(
                to=phone_number,
                text=response_message,
            )
        except Exception as e:
            print(f"Error sending response: {e}")
            import traceback
            traceback.print_exc()

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

