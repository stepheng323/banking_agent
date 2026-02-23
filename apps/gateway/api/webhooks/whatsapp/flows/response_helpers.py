"""Response formatting helpers for flow webhook."""

from typing import Any

from fastapi.responses import JSONResponse, Response

from shared.utils import encrypt_flow_response


def format_encrypted_response(response_data: dict[str, Any], aes_key_bytes: bytes, iv_bytes: bytes) -> Response:
    """Format and encrypt a response."""
    if aes_key_bytes is None or iv_bytes is None:
        return JSONResponse(content={"error": "Encryption keys missing"}, status_code=500)
    encrypted_response = encrypt_flow_response(response_data, aes_key_bytes, iv_bytes)
    return Response(content=encrypted_response, media_type="text/plain")


def format_error_response(
    screen: str,
    error_message: str,
    request_was_encrypted: bool = False,
    aes_key_bytes: bytes = None,
    iv_bytes: bytes = None,
    **extra_data,
) -> Response:
    """Format a standardized error response."""
    response = {
        "screen": screen,
        "data": {"show_error": True, "error_message": error_message, **extra_data},
    }

    if request_was_encrypted:
        return format_encrypted_response(response, aes_key_bytes, iv_bytes)

    return JSONResponse(content=response)


def format_success_response(
    screen: str,
    request_was_encrypted: bool = False,
    aes_key_bytes: bytes = None,
    iv_bytes: bytes = None,
    **data,
) -> Response:
    """Format a standardized success response."""
    response = {
        "screen": screen,
        "data": data,
    }

    if request_was_encrypted:
        return format_encrypted_response(response, aes_key_bytes, iv_bytes)

    return JSONResponse(content=response)


def format_complete_response(
    message: str,
    request_was_encrypted: bool = False,
    aes_key_bytes: bytes = None,
    iv_bytes: bytes = None,
    **extra_data,
) -> Response:
    """Format a response that closes the flow permanently.

    Returns a response with screen: "COMPLETE" which closes the WhatsApp Flow
    and prevents the user from reopening it.
    """
    response = {
        "screen": "COMPLETE",
        "data": {"message": message, **extra_data},
    }

    if request_was_encrypted:
        return format_encrypted_response(response, aes_key_bytes, iv_bytes)

    return JSONResponse(content=response)
