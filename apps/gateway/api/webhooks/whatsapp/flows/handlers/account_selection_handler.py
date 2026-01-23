"""Handler for ACCOUNT_SELECTION screen."""

from fastapi.responses import Response
from pydantic import BaseModel

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import (
    format_error_response,
    format_success_response,
)
from shared.services.onboarding import (
    ServiceResult,
    account_add_service,
    account_service,
    session_manager,
)


class AccountSelectionInput(BaseModel):
    """Input data for account selection screen."""

    bvn: str | None = None
    selected_account: str | None = None


async def handle_account_selection(
    data: AccountSelectionInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
) -> Response:
    """Handle ACCOUNT_SELECTION screen - stores selected account."""

    if not flow_token:
        return format_error_response(
            "ACCOUNT_SELECTION",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    session = await session_manager.get_session(flow_token)
    is_account_linking = session.get("is_account_linking", False) if session else False

    if is_account_linking:
        result = ServiceResult(**await account_add_service.add_account(flow_token, data.selected_account))

        if result.success:
            return format_success_response(
                "SUCCESS",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
                extension_message_response={
                    "params": {
                        "flow_token": flow_token,
                        "success": True,
                    }
                },
            )

        return format_error_response(
            "ACCOUNT_SELECTION",
            result.error,
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            accounts=session.get("accounts", []) if session else [],
        )

    # Normal onboarding - use account_service
    result = ServiceResult(**await account_service.select_account(flow_token, data.selected_account))

    if result.success:
        return format_success_response(
            "PIN_ENTRY",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result.data.get("bvn", ""),
            show_error=False,
            error_message="",
        )

    return format_error_response(
        "ACCOUNT_SELECTION",
        result.error,
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        accounts=result.data.get("accounts", []) if result.data else [],
    )
