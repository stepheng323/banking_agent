"""Handler for METHOD_SELECTION screen."""

from typing import Optional
from pydantic import BaseModel

from fastapi.responses import Response

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import format_error_response, format_success_response
from shared.services.onboarding import bvn_service, ServiceResult


class MethodSelectionInput(BaseModel):
    """Input data for method selection screen."""
    bvn: Optional[str] = None
    method: Optional[str] = None


async def handle_method_selection(
    data: MethodSelectionInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """Handle METHOD_SELECTION screen - sends OTP via chosen method."""
    
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
