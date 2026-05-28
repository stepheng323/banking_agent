"""Handler for METHOD_SELECTION screen in onboarding flow.

This handler is for the onboarding flow where user comes from BVN_ENTRY.
Session data is already populated by bvn_handler.
"""

from fastapi.responses import Response
from pydantic import BaseModel

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import (
    format_error_response,
    format_success_response,
)
from apps.gateway.api.webhooks.whatsapp.flows.session_owner import (
    format_owner_error_response,
    verify_whatsapp_flow_session_owner,
)
from shared.services.onboarding.runtime import bvn_service
from shared.services.onboarding.session import ServiceResult


class MethodSelectionInput(BaseModel):
    """Input data for the method selection screen."""

    bvn: str | None = None
    method: str | None = None


async def handle_method_selection(
    data: MethodSelectionInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    authorizing_channel_user_id: str | None = None,
) -> Response:
    """Handle METHOD_SELECTION screen in onboarding flow - sends OTP via chosen method."""
    owner_check = await verify_whatsapp_flow_session_owner(
        flow_token=flow_token,
        authorizing_channel_user_id=authorizing_channel_user_id,
        screen="METHOD_SELECTION",
    )
    if not owner_check.ok:
        return format_owner_error_response("METHOD_SELECTION", request_was_encrypted, aes_key_bytes, iv_bytes)

    result = ServiceResult(**await bvn_service.send_otp(flow_token, data.method))
    if result.success:
        return format_success_response(
            "OTP_VERIFICATION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result.data["bvn"],
            show_error=False,
            error_message="",
        )

    return format_error_response(
        "METHOD_SELECTION",
        result.error,
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=result.data.get("bvn", "") if result.data else "",
        methods=result.data.get("methods", []) if result.data else [],
    )
