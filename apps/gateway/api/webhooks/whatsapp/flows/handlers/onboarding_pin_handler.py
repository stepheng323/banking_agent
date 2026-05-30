"""Handler for PIN_ENTRY screen (onboarding flow)."""

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
from banking.accounts.onboarding.runtime import account_service
from banking.accounts.onboarding.session import ServiceResult


class OnboardingPinInput(BaseModel):
    """Input data for PIN entry screen."""

    pin: str | None = None
    email: str | None = None
    address: str | None = None
    bvn: str | None = None


async def handle_onboarding_pin(
    data: OnboardingPinInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    authorizing_channel_user_id: str | None = None,
) -> Response:
    """Handle PIN_ENTRY screen - validates PIN, email, address and completes onboarding."""

    if not flow_token:
        return format_error_response(
            "PIN_ENTRY",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    owner_check = await verify_whatsapp_flow_session_owner(
        flow_token=flow_token,
        authorizing_channel_user_id=authorizing_channel_user_id,
        screen="PIN_ENTRY",
    )
    if not owner_check.ok:
        return format_owner_error_response("PIN_ENTRY", request_was_encrypted, aes_key_bytes, iv_bytes)

    result = ServiceResult(
        **await account_service.complete_onboarding(
            flow_token,
            pin=data.pin,
            email=data.email,
            address=data.address,
        )
    )

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
        "PIN_ENTRY",
        result.error,
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
    )
