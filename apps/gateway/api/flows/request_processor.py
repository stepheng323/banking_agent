"""Request processing utilities for flow webhook."""

import json
from typing import Any, Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import Response

from shared.utils import decrypt_flow_data, is_encrypted


class ProcessedRequest:
    """Container for processed flow request data."""

    def __init__(
        self,
        screen: Optional[str],
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


async def process_flow_request(req: Request) -> Tuple[ProcessedRequest, Optional[Response]]:
    """
    Process incoming flow request, handling encryption/decryption.
    
    Returns:
        Tuple of (ProcessedRequest, Optional[Response])
        Response is only set if there's an error that should be returned immediately
    """
    body = await req.json()
    print("📥 Flow webhook received")

    request_was_encrypted = is_encrypted(body)
    aes_key_bytes = None
    iv_bytes = None

    if request_was_encrypted:
        encrypted_data = body["encrypted_flow_data"]
        encrypted_key = body["encrypted_aes_key"]
        iv = body["initial_vector"]

        result = decrypt_flow_data(encrypted_data, encrypted_key, iv)

        print(f"🔍 Result: {result}")

        if not result:
            print("   ❌ Decryption failed - returning HTTP 421 per Meta spec")
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
        print(f"   ✅ Decrypted flow data: {decrypted}")

    else:
        screen = body.get("screen")
        data = body.get("data", {})
        flow_token = body.get("flow_token")
        print("   ℹ️  Unencrypted request - will return plain JSON")

    print(f"   Screen: {screen}")
    print(f"   Data: {data}")
    print(f"   Flow token: {flow_token}")

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

