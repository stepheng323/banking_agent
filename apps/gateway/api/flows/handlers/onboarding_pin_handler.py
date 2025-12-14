"""Handler for PIN_ENTRY screen (onboarding flow)."""

import asyncio
from typing import Optional
from pydantic import BaseModel

from fastapi.responses import Response

from shared.clients.whatsapp_client import WhatsAppClient
from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from shared.services import onboarding_service


class OnboardingPinInput(BaseModel):
    """Input data for PIN entry screen."""
    pin: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    bvn: Optional[str] = None


async def handle_onboarding_pin(
    data: OnboardingPinInput,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
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

    result = await onboarding_service.complete_onboarding(
        flow_token,
        pin=data.pin,
        email=data.email,
        address=data.address,
    )
    
    if result.success:
        phone_number = result.data.get("phone_number", "")
        
        asyncio.create_task(
            whatsapp_client.send_text(
                to=phone_number,
                text="🎉 Welcome to Fusepay! Your account setup is in progress. You'll receive instructions shortly.",
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
