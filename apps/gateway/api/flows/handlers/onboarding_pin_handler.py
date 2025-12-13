"""Handler for PIN_ENTRY screen (onboarding flow)."""

import asyncio
from typing import Optional, List
from pydantic import BaseModel

from fastapi.responses import Response

from shared.clients.whatsapp_client import WhatsAppClient
from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from shared.services import onboarding_service


class OnboardingPinInput(BaseModel):
    """Input data for PIN entry screen."""
    pin: Optional[str] = None
    bvn: Optional[str] = None
    selected_accounts: List[str] = []


async def handle_onboarding_pin(
    data: OnboardingPinInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
) -> Response:
    """Handle PIN_ENTRY screen - validates PIN, creates user, links accounts."""
    
    if not flow_token:
        return format_error_response(
            "PIN_ENTRY",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    result = await onboarding_service.complete_onboarding(flow_token, data.pin)
    
    if result.success:
        phone_number = result.data.get("phone_number", "")
        
        asyncio.create_task(
            whatsapp_client.send_text(
                to=phone_number,
                text="🎉 Welcome to Fusepay! Your onboarding is complete. You can now start using the app to send and receive money.",
            )
        )
        
        return format_success_response(
            "SUCCESS",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            extension_message_response={
                "params": {
                    "flow_token": flow_token,
                    "bvn": result.data.get("bvn"),
                    "accounts_count": result.data.get("accounts_count", 0),
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
