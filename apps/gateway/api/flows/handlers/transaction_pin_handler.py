"""Unified PIN handler for all transaction types (transfer, airtime, data, batch)."""

from typing import Any, Dict, Optional, TYPE_CHECKING

from fastapi.responses import Response

from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp_client import WhatsAppClient
from apps.core.src.agent.tools.authorization.service import AuthorizationService
from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.transfer.service import TransferService
    from apps.core.src.agent.sub_agents.airtime.service import AirtimeService
    from apps.core.src.agent.tools.batch.service import BatchService
else:
    try:
        from apps.core.src.agent.sub_agents.transfer.service import TransferService
        from apps.core.src.agent.sub_agents.airtime.service import AirtimeService
        from apps.core.src.agent.tools.batch.service import BatchService
    except ImportError:
        TransferService = None      
        AirtimeService = None   
        BatchService = None


async def handle_transaction_pin(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
    transfer_service: Optional["TransferService"] = None,
    airtime_service: Optional["AirtimeService"] = None,
    batch_service: Optional["BatchService"] = None,
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
        batch_service: Optional BatchService instance
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
    
    if transaction_type == "batch":
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

    auth_result = await authorization_service.verify_pin(
        phone_number, 
        str(pin), 
        idem_key,
        transaction_type=transaction_type
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
        response_message = None
        
        service_map = {
            "transfer": transfer_service,
            "airtime": airtime_service,
            "batch": batch_service
        }
        
        service = service_map.get(transaction_type)
        
        if transaction_type == "transfer" or transaction_type == "airtime":
            if service and hasattr(service, "graph") and hasattr(service.graph, "resume_after_pin_verification"):
                try:
                    response_message = await service.graph.resume_after_pin_verification(
                        phone_number, True, None
                    )
                except Exception:
                    import traceback
                    traceback.print_exc()
                    if transaction_type == "airtime":
                        response_message = f"{transaction_type.capitalize()} transaction authorized. Processing your request..."
                    else:
                        raise
            else:
                raise ValueError(f"Service for {transaction_type} not properly initialized")
                
        elif transaction_type == "batch":
            if service and hasattr(service, "resume_after_pin_verification"):
                response_message = await service.resume_after_pin_verification(
                    phone_number, True, None
                )
            else:
                raise ValueError("Batch service not available")
                
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

    if not response_message or not response_message.strip():
        response_message = f"{transaction_type.capitalize()} transaction authorized. Processing your request..."

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
