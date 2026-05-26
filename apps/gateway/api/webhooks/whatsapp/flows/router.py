"""Main router for flow webhook endpoint."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from apps.gateway.api.webhooks.whatsapp.flows.dependencies import (
    get_queue_publisher,
    get_whatsapp_client,
)
from apps.gateway.api.webhooks.whatsapp.flows.handlers.account_selection_handler import (
    AccountSelectionInput,
    handle_account_selection,
)
from apps.gateway.api.webhooks.whatsapp.flows.handlers.bvn_handler import handle_bvn_entry
from apps.gateway.api.webhooks.whatsapp.flows.handlers.channel_link_pin_handler import (
    handle_channel_link_pin,
)
from apps.gateway.api.webhooks.whatsapp.flows.handlers.linking_method_selection_handler import (
    LinkingMethodSelectionInput,
    handle_linking_method_selection,
)
from apps.gateway.api.webhooks.whatsapp.flows.handlers.method_selection_handler import (
    MethodSelectionInput,
    handle_method_selection,
)
from apps.gateway.api.webhooks.whatsapp.flows.handlers.onboarding_pin_handler import (
    OnboardingPinInput,
    handle_onboarding_pin,
)
from apps.gateway.api.webhooks.whatsapp.flows.handlers.otp_handler import (
    OtpVerificationInput,
    handle_otp_verification,
)
from apps.gateway.api.webhooks.whatsapp.flows.handlers.transaction_pin_handler import (
    handle_transaction_pin,
    parse_transaction_pin_flow_token,
)
from apps.gateway.api.webhooks.whatsapp.flows.request_processor import process_flow_request
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.adapter import QueuePublisher
from shared.services.channel_linking import is_channel_link_pin_token
from shared.utils.flow_encryption import encrypt_flow_response
from shared.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _flow_health_response(version: str | None = None) -> dict[str, object]:
    return {"version": version or "3.0", "data": {"status": "active"}}


def _format_flow_response(
    response_data: dict[str, object],
    *,
    request_was_encrypted: bool,
    aes_key_bytes: bytes | None,
    iv_bytes: bytes | None,
) -> Response:
    if request_was_encrypted:
        if aes_key_bytes is None or iv_bytes is None:
            return JSONResponse(content={"error": "Encryption keys missing"}, status_code=500)
        encrypted_response = encrypt_flow_response(response_data, aes_key_bytes, iv_bytes)
        return Response(content=encrypted_response, media_type="text/plain")

    return Response(content=json.dumps(response_data), media_type="text/plain")


def _has_pin_submission_data(data: dict[str, object]) -> bool:
    return any(key in data for key in ("pin", "transaction_pin", "transactionPin", "pin_code", "pinCode"))


def _is_pin_flow_token(flow_token: str | None) -> bool:
    return is_channel_link_pin_token(flow_token) or parse_transaction_pin_flow_token(flow_token) is not None


@router.post("/webhook/flow")
async def flow_webhook(
    req: Request,
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
    queue_publisher: QueuePublisher = Depends(get_queue_publisher),
):
    """
    Handle WhatsApp Flow data exchange.
    This is called when user interacts with flow screens or for health checks.

    Note: Agent services are called via queue events, not directly.
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
        action = processed_request.action
        version = processed_request.version
        request_was_encrypted = processed_request.request_was_encrypted
        aes_key_bytes = processed_request.aes_key_bytes
        iv_bytes = processed_request.iv_bytes
        authorizing_channel_user_id = processed_request.authorizing_channel_user_id
        normalized_action = (action or "").strip().lower()

        if normalized_action == "ping" and screen is None:
            logger.info("whatsapp_flow_health_check", version=version, request_was_encrypted=request_was_encrypted)
            return _format_flow_response(
                _flow_health_response(version),
                request_was_encrypted=request_was_encrypted,
                aes_key_bytes=aes_key_bytes,
                iv_bytes=iv_bytes,
            )

        if screen is None and _has_pin_submission_data(data) and not flow_token:
            logger.warning("whatsapp_flow_pin_submit_missing_flow_token", action=action, version=version)
            return JSONResponse(content={"error": "Missing flow token"}, status_code=400)

        if screen is None and flow_token and _has_pin_submission_data(data):
            logger.info(
                "whatsapp_flow_pin_screen_inferred",
                action=action,
                version=version,
                flow_token_type="channel_link" if is_channel_link_pin_token(flow_token) else "transaction",
            )
            screen = "Pin"

        if normalized_action != "ping" and screen is None and _is_pin_flow_token(flow_token):
            logger.info("whatsapp_flow_pin_init", action=action, version=version)
            return _format_flow_response(
                {"version": version or "3.0", "screen": "Pin", "data": {}},
                request_was_encrypted=request_was_encrypted,
                aes_key_bytes=aes_key_bytes,
                iv_bytes=iv_bytes,
            )

        if screen == "BVN_ENTRY":
            return await handle_bvn_entry(
                data.get("bvn", "").strip(),
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
                authorizing_channel_user_id=authorizing_channel_user_id,
            )

        elif screen == "METHOD_SELECTION":
            is_linking_flow = flow_token and flow_token.startswith("link-")
            if is_linking_flow:
                method_data = LinkingMethodSelectionInput(**data)
                return await handle_linking_method_selection(
                    method_data,
                    flow_token or "",
                    request_was_encrypted,
                    aes_key_bytes or b"",
                    iv_bytes or b"",
                    authorizing_channel_user_id=authorizing_channel_user_id,
                )
            else:
                method_data = MethodSelectionInput(**data)
                return await handle_method_selection(
                    method_data,
                    flow_token or "",
                    request_was_encrypted,
                    aes_key_bytes or b"",
                    iv_bytes or b"",
                    authorizing_channel_user_id=authorizing_channel_user_id,
                )

        elif screen == "OTP_VERIFICATION":
            otp_data = OtpVerificationInput(**data)
            return await handle_otp_verification(
                otp_data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
                authorizing_channel_user_id=authorizing_channel_user_id,
            )

        elif screen == "ACCOUNT_SELECTION":
            account_data = AccountSelectionInput(**data)
            return await handle_account_selection(
                account_data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
                authorizing_channel_user_id=authorizing_channel_user_id,
            )

        elif screen == "PIN_ENTRY":
            pin_data = OnboardingPinInput(**data)
            return await handle_onboarding_pin(
                pin_data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
                authorizing_channel_user_id=authorizing_channel_user_id,
            )

        elif screen == "Pin":
            if is_channel_link_pin_token(flow_token):
                return await handle_channel_link_pin(
                    data,
                    flow_token or "",
                    request_was_encrypted,
                    aes_key_bytes or b"",
                    iv_bytes or b"",
                    authorizing_channel_user_id=authorizing_channel_user_id,
                )
            return await handle_transaction_pin(
                data,
                flow_token or "",
                request_was_encrypted,
                aes_key_bytes or b"",
                iv_bytes or b"",
                whatsapp_client,
                publisher=queue_publisher,
                authorizing_channel_user_id=authorizing_channel_user_id,
            )

        logger.warning("whatsapp_flow_unknown_screen", screen=screen, action=action, version=version)
        return _format_flow_response(
            _flow_health_response(version),
            request_was_encrypted=request_was_encrypted,
            aes_key_bytes=aes_key_bytes,
            iv_bytes=iv_bytes,
        )

    except Exception as e:
        logger.error("whatsapp_flow_webhook_error", error_type=type(e).__name__, exc_info=True)
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)
