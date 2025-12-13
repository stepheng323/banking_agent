"""Main router for flow webhook endpoint."""

import json
import traceback

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from shared.clients.whatsapp_client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.utils import encrypt_flow_response

from apps.gateway.api.flows.dependencies import (
    get_redis_queue,
    get_whatsapp_client,
    get_airtime_service,
    get_transfer_service,
    get_batch_service,
)
from apps.gateway.api.flows.handlers.account_selection_handler import handle_account_selection, AccountSelectionInput
from apps.gateway.api.flows.handlers.bvn_handler import handle_bvn_entry
from apps.gateway.api.flows.handlers.method_selection_handler import handle_method_selection, MethodSelectionInput
from apps.gateway.api.flows.handlers.onboarding_pin_handler import handle_onboarding_pin, OnboardingPinInput
from apps.gateway.api.flows.handlers.otp_handler import handle_otp_verification, OtpVerificationInput
from apps.gateway.api.flows.handlers.transaction_pin_handler import handle_transaction_pin
from apps.gateway.api.flows.request_processor import  process_flow_request

router = APIRouter()


@router.post("/webhook/flow")
async def flow_webhook(
    req: Request,
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
    queue: RedisQueue = Depends(get_redis_queue),
    airtime_service = Depends(get_airtime_service),
    transfer_service = Depends(get_transfer_service),
    batch_service = Depends(get_batch_service),
):
    """
    Handle WhatsApp Flow data exchange.
    This is called when user interacts with flow screens or for health checks.
    """
    try:
        processed_request, error_response = await process_flow_request(req)
        if error_response:
            return error_response

        if not processed_request:
            return JSONResponse(content={"error": "Failed to process request"}, status_code=400)

        screen = processed_request.screen
        data = processed_request.data
        flow_token = processed_request.flow_token
        request_was_encrypted = processed_request.request_was_encrypted
        aes_key_bytes = processed_request.aes_key_bytes
        iv_bytes = processed_request.iv_bytes


        if screen == "BVN_ENTRY":
            return await handle_bvn_entry(
                data.get("bvn", "").strip(),
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
            )

        elif screen == "METHOD_SELECTION":
            method_data = MethodSelectionInput(**data)
            return await handle_method_selection(
                method_data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
            )

        elif screen == "OTP_VERIFICATION":
            otp_data = OtpVerificationInput(**data)
            return await handle_otp_verification(
                otp_data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
            )

        elif screen == "ACCOUNT_SELECTION":
            account_data = AccountSelectionInput(**data)
            return await handle_account_selection(
                account_data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
            )

        elif screen == "PIN_ENTRY":
            pin_data = OnboardingPinInput(**data)
            return await handle_onboarding_pin(
                pin_data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
                whatsapp_client,
            )

        elif screen == "Pin":
            return await handle_transaction_pin(
                data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
                whatsapp_client,
                transfer_service=transfer_service,
                airtime_service=airtime_service,
                batch_service=batch_service,
            )

        print(f" 🏥 Health check (unknown screen: {screen})")
        health_response = {"data": {"status": "active"}}

        if request_was_encrypted:
            if aes_key_bytes is None or iv_bytes is None:
                return JSONResponse(
                    content={"error": "Encryption keys missing"}, status_code=500
                )
            encrypted_response = encrypt_flow_response(
                health_response, aes_key_bytes, iv_bytes
            )
            return Response(content=encrypted_response, media_type="text/plain")

        return Response(content=json.dumps(health_response), media_type="text/plain")

    except Exception as e:
        print(f"❌ Error in flow webhook: {e}")
        traceback.print_exc()
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)
