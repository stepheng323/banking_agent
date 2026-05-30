"""Handler for OTP_VERIFICATION screen."""

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
from banking.accounts.onboarding.runtime import bvn_service
from banking.accounts.onboarding.session import ServiceResult


class OtpVerificationInput(BaseModel):
    """Input data for OTP verification screen."""

    otp: str | None = None
    bvn: str | None = None


async def handle_otp_verification(
    data: OtpVerificationInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    authorizing_channel_user_id: str | None = None,
) -> Response:
    """Handle OTP_VERIFICATION screen - validates OTP and fetches bank accounts."""
    owner_check = await verify_whatsapp_flow_session_owner(
        flow_token=flow_token,
        authorizing_channel_user_id=authorizing_channel_user_id,
        screen="OTP_VERIFICATION",
    )
    if not owner_check.ok:
        return format_owner_error_response("OTP_VERIFICATION", request_was_encrypted, aes_key_bytes, iv_bytes)

    result = ServiceResult(**await bvn_service.verify_otp(flow_token, data.otp or ""))

    if result.success:
        result_data = result.data or {}
        return format_success_response(
            "ACCOUNT_SELECTION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result_data["bvn"],
            accounts=result_data["accounts"],
            show_error=False,
            error_message="",
        )

    return format_error_response(
        "OTP_VERIFICATION",
        result.error or "",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=data.bvn or "",
    )
