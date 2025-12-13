"""Handler for ACCOUNT_SELECTION screen."""

from typing import Optional, List
from pydantic import BaseModel

from fastapi.responses import Response

from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from shared.services import onboarding_service


class AccountSelectionInput(BaseModel):
    """Input data for account selection screen."""
    bvn: Optional[str] = None
    selected_accounts: List[str] = []


async def handle_account_selection(
    data: AccountSelectionInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """Handle ACCOUNT_SELECTION screen - stores selected accounts."""
    
    if not flow_token:
        return format_error_response(
            "ACCOUNT_SELECTION",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    result = await onboarding_service.select_accounts(flow_token, data.selected_accounts)
    
    if result.success:
        return format_success_response(
            "PIN_ENTRY",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result.data.get("bvn", ""),
            selected_accounts=result.data.get("selected_accounts", []),
            show_error=False,
            error_message="",
        )
    
    return format_error_response(
        "ACCOUNT_SELECTION",
        result.error,
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
    )
