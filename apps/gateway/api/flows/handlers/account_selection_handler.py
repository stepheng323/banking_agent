"""Handler for ACCOUNT_SELECTION screen."""

from typing import Any, Dict

from fastapi.responses import Response

from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from apps.gateway.api.flows.verification import update_verification_data


async def handle_account_selection(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """
    Handle ACCOUNT_SELECTION screen.
    
    Stores selected accounts and proceeds to PIN_ENTRY screen.
    """
    if not flow_token:
        return format_error_response(
            "ACCOUNT_SELECTION",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    accounts = data.get("selected_accounts", [])
    if flow_token:
        await update_verification_data(flow_token, {"selected_accounts": accounts})

    return format_success_response(
        "PIN_ENTRY",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        show_error=False,
        error_message="",
    )

