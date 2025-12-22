"""Request processing utilities for flow webhook."""

import json
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import Request
from fastapi.responses import Response

from shared.utils import decrypt_flow_data, is_encrypted


ScreenType = Literal[
    "BVN_ENTRY",
    "METHOD_SELECTION",
    "OTP_VERIFICATION",
    "ACCOUNT_SELECTION",
    "PIN_ENTRY",
    "Pin",
    "SUCCESS",
]


class ProcessedRequest:
    """Container for processed flow request data."""

    def __init__(
        self,
        screen: Optional[ScreenType],
        data: Dict[str, Any],
        flow_token: Optional[str],
        request_was_encrypted: bool,
        aes_key_bytes: Optional[bytes] = None,
        iv_bytes: Optional[bytes] = None,
    ):
        self.screen = screen
        self.data = data
        self.flow_token = flow_token
        self.request_was_encrypted = request_was_encrypted
        self.aes_key_bytes = aes_key_bytes
        self.iv_bytes = iv_bytes


async def process_flow_request(req: Request) -> Tuple[Optional[ProcessedRequest], Optional[Response]]:
    """
    Process incoming flow request, handling encryption/decryption.
    
    Returns:
        Tuple of (ProcessedRequest, Optional[Response])
        Response is only set if there's an error that should be returned immediately
    """
    try:
        body = await req.json()
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        import traceback
        traceback.print_exc()
        return (
            None,
            Response(
                content=json.dumps({"error": "Invalid JSON"}),
                media_type="text/plain",
                status_code=400,
            ),
        )
    

    request_was_encrypted = is_encrypted(body)
    aes_key_bytes = None
    iv_bytes = None

    if request_was_encrypted:
        encrypted_data = body["encrypted_flow_data"]
        encrypted_key = body["encrypted_aes_key"]
        iv = body["initial_vector"]

        result = decrypt_flow_data(encrypted_data, encrypted_key, iv)

        if not result:
            error_response = {
                "errors": [
                    {
                        "message": "Failed to decrypt request. Please check your encryption configuration."
                    }
                ]
            }
            return (
                None,
                Response(
                    content=json.dumps(error_response),
                    media_type="text/plain",
                    status_code=421,
                ),
            )

        decrypted, aes_key_bytes, iv_bytes = result
        screen = decrypted.get("screen")
        data = decrypted.get("data", {})
        flow_token = decrypted.get("flow_token")

    else:
        screen = body.get("screen")
        data = body.get("data", {})
        flow_token = body.get("flow_token")


    return (
        ProcessedRequest(
            screen=screen,
            data=data,
            flow_token=flow_token,
            request_was_encrypted=request_was_encrypted,
            aes_key_bytes=aes_key_bytes,
            iv_bytes=iv_bytes,
        ),
        None,
    )

