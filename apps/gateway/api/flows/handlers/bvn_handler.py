"""Handler for BVN_ENTRY screen."""

from typing import Any, Dict, Tuple

from fastapi.responses import Response

from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from apps.gateway.api.flows.verification import set_verification_data


async def handle_bvn_entry(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """
    Handle BVN_ENTRY screen.
    
    Validates BVN format and proceeds to OTP_VERIFICATION screen.
    """
    bvn = data.get("bvn")
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

    print(f"🔍 Verifying BVN: {bvn}")

    is_valid = bvn and len(bvn) == 11 and bvn.isdigit()

    if is_valid:
        await set_verification_data(flow_token, {
            "phone_number": flow_token.split("_")[-1],
            "bvn": bvn,
            "bvn_verified": True,
        })

        print("✅ BVN verified successfully")
        return format_success_response(
            "OTP_VERIFICATION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=str(bvn),
            show_error=False,
            error_message="",
        )

    print("❌ BVN verification failed")
    return format_error_response(
        "BVN_ENTRY",
        "Invalid BVN. Please check and enter a valid 11-digit BVN.",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=str(bvn) if bvn else "",
    )

