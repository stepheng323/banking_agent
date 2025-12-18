"""Authorization node for transfer flow."""

from typing import cast

from apps.core.src.agent.sub_agents.transfer.state import TransferState
from apps.gateway.api.flows.transaction_service import create_transfer_transaction
from apps.core.src.agent.orchestrator.features.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from shared.services.auth import AuthorizationService
from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue
from apps.core.src.agent.sub_agents.transfer.nodes.utils import debug_log
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def authorize_transaction(
    state: TransferState,
    redis_client: Redis,
    queue: RedisQueue,
    authorization_service: AuthorizationService | None = None,
) -> TransferState:
    """Authorize transaction after PIN verification."""
    phone_number = state.get("phone_number")
    idem_key = state.get("idempotency_key")
    synthesizer = get_synthesizer()
    
    debug_log(f"🔐 authorize_transaction ENTRY: phone={phone_number}, idem_key={idem_key}, flow_state={state.get('flow_state')}, transfer_status={state.get('transfer_status')}")

    if not idem_key:
        context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
        response = await synthesizer.synthesize(context)
        return cast(
            TransferState,
            {
                **state,
                "response": response,
                "flow_state": "error",
                "transfer_status": "failed",
            },
        )

    if not authorization_service:
        authorization_service = AuthorizationService(redis_client=redis_client)
    
    pin_result = await authorization_service.get_pin_verification_result(idem_key)
    pin_verified_in_state = state.get("pin_verified")

    if not pin_result:
        if pin_verified_in_state is True:
            pass
        else:
            return cast(
                TransferState,
                {
                    **state,
                    "response": "",  # No response needed, WhatsApp Flow handles PIN
                    "flow_state": "authorizing",
                },
            )
    elif not pin_result.verified:
        error_msg = pin_result.error or "PIN verification failed"
        retry_count = pin_result.retry_count

        if retry_count >= 3:
            await redis_client.delete(f"user:{phone_number}:pending_transfer")
            context = build_response_context(
                ResponseIntent.MAX_ATTEMPTS_EXCEEDED, 
                state,
                error_message=error_msg
            )
            response = await synthesizer.synthesize(context)
            return cast(
                TransferState,
                {
                    **state,
                    "response": response,
                    "flow_state": "error",
                    "transfer_status": "failed",
                    "pin_verified": False,
                    "pin_verification_error": error_msg,
                    "pin_retry_count": retry_count,
                },
            )

        context = build_response_context(
            ResponseIntent.PIN_FAILED,
            state,
            error_message=error_msg
        )
        response = await synthesizer.synthesize(context)
        return cast(
            TransferState,
            {
                **state,
                "response": response,
                "flow_state": "confirming",
                "pin_verified": False,
                "pin_verification_error": error_msg,
                "pin_retry_count": retry_count,
            },
        )

    try:
        amount = state.get("amount")
        recipient_account = state.get("recipient_account")
        recipient_bank_name = state.get("recipient_bank_name")
        recipient_bank_code = state.get("recipient_bank_code")
        recipient_name = state.get("recipient_name")
        
        if not amount or not recipient_account:
            logger.warning(
                "authorization_missing_data",
                amount=amount,
                recipient_account=recipient_account,
            )
            context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
            response = await synthesizer.synthesize(context)
            return {
                **state,
                "response": response,
                "transfer_status": "failed",
                "flow_state": "completed",
            }

        pending_transfer = {
            "amount": amount,
            "recipient": {
                "account_number": recipient_account,
                "bank_name": recipient_bank_name,
                "bank_code": recipient_bank_code,
                "name": recipient_name
            },
            "idempotency_key": state.get("idempotency_key")
        }

        user_id = None
        if pin_result and pin_result.user_id:
            user_id = pin_result.user_id
        else:
            user_profile = state.get("user_profile")
            if isinstance(user_profile, dict):
                user_id = user_profile.get("id")
            if not user_id:
                logger.warning(
                    "authorization_missing_user_id",
                    pin_result=bool(pin_result),
                    user_profile=bool(user_profile),
                )
                context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
                response = await synthesizer.synthesize(context)
                return cast(
                    TransferState,
                    {
                        **state,
                        "response": response,
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

        prev_values_key = f"transfer:prev:session:{phone_number}"
        await redis_client.delete(prev_values_key)

        pin_verification_key = f"transaction:pin_verified:{idem_key}"
        await redis_client.delete(pin_verification_key)

        retry_count = pin_result.retry_count if pin_result else 0
        # Success response - keeping simple for now as success is shown via completion message
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
        logger.error(
            "authorization_error",
            error=str(e),
            phone=phone_number,
            exc_info=True,
        )
        context = build_response_context(
            ResponseIntent.TRANSFER_FAILED,
            state,
            error_message="Failed to process authorization. Please try again."
        )
        response = await synthesizer.synthesize(context)
        return cast(
            TransferState,
            {
                **state,
                "response": response,
                "flow_state": "error",
                "transfer_status": "failed",
            },
        )
