"""Handler for BVN_ENTRY screen."""

from fastapi.responses import Response

from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from shared.services import onboarding_service


async def handle_bvn_entry(
    bvn: str,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """Handle BVN_ENTRY screen - validates BVN and initiates verification."""
    
    if not bvn:
        return format_error_response(
            "BVN_ENTRY",
            "BVN is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    if not flow_token:
        return format_error_response(
            "BVN_ENTRY",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    result = await onboarding_service.initiate_bvn_verification(flow_token, bvn)
    
    if result.success:
        return format_success_response(
            "METHOD_SELECTION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result.data["bvn"],
            methods=result.data["methods"],
            show_error=False,
            error_message="",
        )
    
    return format_error_response(
        "BVN_ENTRY",
        result.error,
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=bvn,
    )
