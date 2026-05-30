"""Handler for METHOD_SELECTION screen in account linking flow.

This handler is for the dedicated account linking flow where
METHOD_SELECTION is the first screen (no BVN_ENTRY).
"""

import hashlib

from fastapi.responses import Response
from pydantic import BaseModel

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import format_error_response, format_success_response
from apps.gateway.api.webhooks.whatsapp.flows.session_owner import (
    format_owner_error_response,
    verify_whatsapp_flow_session_owner,
)
from banking.accounts.onboarding.runtime import bvn_service
from banking.accounts.onboarding.session import ServiceResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _token_fingerprint(flow_token: str | None) -> str:
    if not flow_token:
        return ""
    return hashlib.sha256(str(flow_token).encode("utf-8")).hexdigest()[:16]


class LinkingMethodSelectionInput(BaseModel):
    """Input data for method selection in account linking."""

    bvn: str | None = None
    method: str | None = None


async def handle_linking_method_selection(
    data: LinkingMethodSelectionInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    authorizing_channel_user_id: str | None = None,
) -> Response:
    """Handle METHOD_SELECTION for account linking flow.

    On the initial load (no method): Return stored session data with methods.
    On submitting (method selected): Send OTP via chosen method.
    """
    flow_token_hash = _token_fingerprint(flow_token)
    logger.info("linking_method_selection_called", flow_token_hash=flow_token_hash, method=data.method)

    owner_check = await verify_whatsapp_flow_session_owner(
        flow_token=flow_token,
        authorizing_channel_user_id=authorizing_channel_user_id,
        screen="METHOD_SELECTION",
    )
    if not owner_check.ok:
        return format_owner_error_response("METHOD_SELECTION", request_was_encrypted, aes_key_bytes, iv_bytes)

    if not data.method:
        session_data = owner_check.session or await bvn_service.get_session_data(flow_token)
        logger.info(
            "linking_session_data",
            flow_token_hash=flow_token_hash,
            has_session=bool(session_data),
            session_keys=list(session_data.keys()) if session_data else [],
        )

        if session_data:
            methods = session_data.get("methods", [])
            bvn = session_data.get("bvn", "")
            logger.info("linking_returning_methods", methods_count=len(methods), bvn_present=bool(bvn))
            return format_success_response(
                "METHOD_SELECTION",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
                bvn=bvn,
                methods=methods,
            )
        else:
            logger.warning("linking_session_not_found", flow_token_hash=flow_token_hash)
            return format_error_response(
                "METHOD_SELECTION",
                "Session expired. Please start the linking process again.",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

    result = ServiceResult(**await bvn_service.send_otp(flow_token, data.method or ""))
    if result.success:
        result_data = result.data or {}
        return format_success_response(
            "OTP_VERIFICATION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result_data["bvn"],
            show_error=False,
            error_message="",
        )

    return format_error_response(
        "METHOD_SELECTION",
        result.error or "",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=result.data.get("bvn", "") if result.data else "",
        methods=result.data.get("methods", []) if result.data else [],
    )
