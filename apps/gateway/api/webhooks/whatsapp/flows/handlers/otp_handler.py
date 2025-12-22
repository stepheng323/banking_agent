"""Handler for OTP_VERIFICATION screen."""

from typing import Optional
from pydantic import BaseModel

from fastapi.responses import Response

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import format_error_response, format_success_response
from shared.services.onboarding import bvn_service, ServiceResult


class OtpVerificationInput(BaseModel):
    """Input data for OTP verification screen."""
    otp: Optional[str] = None
    bvn: Optional[str] = None


async def handle_otp_verification(
    data: OtpVerificationInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """Handle OTP_VERIFICATION screen - validates OTP and fetches bank accounts."""
    
    result = ServiceResult(**await bvn_service.verify_otp(flow_token, data.otp))
    
    if result.success:
        return format_success_response(
            "ACCOUNT_SELECTION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result.data["bvn"],
            accounts=result.data["accounts"],
            show_error=False,
            error_message="",
        )
    
    return format_error_response(
        "OTP_VERIFICATION",
        result.error,
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=data.bvn or "",
    )
