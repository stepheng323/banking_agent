"""Authorization node for airtime purchase flow."""

import json
from typing import cast

from apps.core.src.agent.orchestrator.features.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue
from shared.services.auth import AuthorizationService
from shared.services.transactions import create_airtime_transaction
from shared.utils.logging import get_logger

from ..graph.utils import debug_log

logger = get_logger(__name__)


async def authorize_transaction(
    state: AirtimeState,
    redis_client: Redis,
    queue: RedisQueue,
    authorization_service: AuthorizationService,
) -> AirtimeState:
    """Authorize transaction after PIN verification."""
    phone_number = state.get("phone_number")
    idem_key = state.get("idempotency_key")
    synthesizer = get_synthesizer()

    if not idem_key:
        context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "response": response,
                "flow_state": "error",
                "airtime_status": "failed",
            },
        )

    pin_result = await authorization_service.get_pin_verification_result(idem_key)

    if not pin_result:
        debug_log(f"⚠️  No PIN verification result found for idem_key: {idem_key}")
        return cast(
            AirtimeState,
            {
                **state,
                "response": "",  # No response needed, WhatsApp Flow handles PIN
                "flow_state": "authorizing",
            },
        )

    if not pin_result.verified:
        error_msg = pin_result.error or "PIN verification failed"
        retry_count = pin_result.retry_count

        if retry_count >= 3:
            debug_log(f"❌ Max PIN retries exceeded for airtime: {idem_key}")
            await redis_client.delete(f"user:{phone_number}:pending_airtime")
            context = build_response_context(
                ResponseIntent.MAX_ATTEMPTS_EXCEEDED, state, error_message=error_msg
            )
            response = await synthesizer.synthesize(context)
            return cast(
                AirtimeState,
                {
                    **state,
                    "response": response,
                    "flow_state": "error",
                    "airtime_status": "failed",
                    "pin_verified": False,
                    "pin_verification_error": error_msg,
                    "pin_retry_count": retry_count,
                },
            )

        debug_log(f"⚠️  PIN verification failed (attempt {retry_count}/3) for airtime: {idem_key}")
        context = build_response_context(ResponseIntent.PIN_FAILED, state, error_message=error_msg)
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "response": response,
                "flow_state": "confirming",
                "pin_verified": False,
                "pin_verification_error": error_msg,
                "pin_retry_count": retry_count,
            },
        )

    debug_log(f"✓ PIN verified for airtime: {idem_key} (user_id={pin_result.user_id})")

    try:
        pending_data = await redis_client.get(f"user:{phone_number}:pending_airtime")
        if not pending_data:
            context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
            response = await synthesizer.synthesize(context)
            return cast(
                AirtimeState,
                {
                    **state,
                    "response": response,
                    "flow_state": "error",
                    "airtime_status": "failed",
                },
            )

        pending_airtime = json.loads(pending_data)

        if not pin_result.user_id:
            context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
            response = await synthesizer.synthesize(context)
            return cast(
                AirtimeState,
                {
                    **state,
                    "response": response,
                    "flow_state": "error",
                    "airtime_status": "failed",
                },
            )

        transaction_id = await create_airtime_transaction(
            pending_airtime,
            pin_result.user_id,
            idem_key,
        )

        airtime_request = {
            "type": "execute_airtime",
            "phone_number": phone_number,
            "idempotency_key": idem_key,
            "airtime_data": pending_airtime,
            "transaction_id": transaction_id,
        }

        await queue.enqueue_simple(
            queue_name="banking:transactions",
            message=airtime_request,
        )

        debug_log(f"✓ Airtime purchase queued for execution: {idem_key}")

        pin_verification_key = f"transaction:pin_verified:{idem_key}"
        await redis_client.delete(pin_verification_key)

        # Return empty response - the executor will send the final success/failure notification
        # This prevents race condition between auth message and executor notification
        return cast(
            AirtimeState,
            {
                **state,
                "response": "",  # Empty - executor sends final notification
                "flow_state": "completed",
                "airtime_status": "authorized",
                "pin_verified": True,
                "pin_retry_count": pin_result.retry_count,
            },
        )

    except Exception as e:
        logger.error(
            "airtime_authorization_error",
            error=str(e),
            phone=phone_number,
            exc_info=True,
        )
        context = build_response_context(
            ResponseIntent.AIRTIME_FAILED,
            state,
            error_message="Failed to process authorization. Please try again.",
        )
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "response": response,
                "flow_state": "error",
                "airtime_status": "failed",
            },
        )
