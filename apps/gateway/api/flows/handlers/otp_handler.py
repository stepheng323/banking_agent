"""Handler for OTP_VERIFICATION screen."""

import uuid
from typing import Any, Dict

from fastapi.responses import Response

from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from apps.gateway.api.flows.verification import get_verification_data, update_verification_data


async def handle_otp_verification(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """
    Handle OTP_VERIFICATION screen.
    
    Validates OTP and proceeds to ACCOUNT_SELECTION screen.
    """
    otp = data.get("otp")
    bvn = data.get("bvn")

    if not otp:
        return format_error_response(
            "OTP_VERIFICATION",
            "OTP is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    print(f"🔍 Verifying OTP: {otp}")

    is_valid = otp and len(otp) == 6 and otp.isdigit()

    if is_valid:
        if flow_token:
            await update_verification_data(flow_token, {
                "otp_verified": True,
                "otp": otp,
            })

        print("✅ OTP verified successfully")

        # Fetch user's bank accounts (mock data for now)
        # In production, fetch from your banking API using BVN
        accounts_full = [
            {
                "id": str(uuid.uuid4()),
                "account_number": "0760505261",
                "bank_name": "Access Bank",
                "account_name": "John Doe",
            },
            {
                "id": str(uuid.uuid4()),
                "account_number": "0123456789",
                "bank_name": "GTBank",
                "account_name": "John Doe",
            },
            {
                "id": str(uuid.uuid4()),
                "account_number": "9876543210",
                "bank_name": "Zenith Bank",
                "account_name": "John Doe",
            },
        ]

        # Persist accounts for later steps so IDs remain consistent
        if flow_token:
            await update_verification_data(flow_token, {
                "accounts_full": accounts_full,
            })

        accounts_flow = [
            {
                "id": acc["id"],
                "title": f"{acc['bank_name']} - {acc['account_number']}",
            }
            for acc in accounts_full
        ]

        # Get BVN from storage or current data
        verification_data = await get_verification_data(flow_token) if flow_token else {}
        stored_bvn = verification_data.get("bvn")
        current_bvn = stored_bvn or bvn or ""

        return format_success_response(
            "ACCOUNT_SELECTION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            accounts=accounts_flow,
            bvn=str(current_bvn),
            show_error=False,
            error_message="",
        )

    print("❌ OTP verification failed")

    verification_data = await get_verification_data(flow_token) if flow_token else {}
    stored_bvn = verification_data.get("bvn")
    current_bvn = stored_bvn or bvn or ""

    return format_error_response(
        "OTP_VERIFICATION",
        "Invalid OTP. Please check and enter the correct 6-digit OTP.",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=str(current_bvn),
    )

