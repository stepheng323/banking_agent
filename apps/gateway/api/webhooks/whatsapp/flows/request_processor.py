"""Request processing utilities for flow webhook."""

import json
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import Response

from shared.config.settings import settings
from shared.utils.flow_decryption import decrypt_flow_data, is_encrypted
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

ScreenType = Literal[
    "BVN_ENTRY",
    "METHOD_SELECTION",
    "OTP_VERIFICATION",
    "ACCOUNT_SELECTION",
    "PIN_ENTRY",
    "Pin",
    "SUCCESS",
]

_ENCRYPTED_REQUEST_FIELDS = frozenset({"encrypted_flow_data", "encrypted_aes_key", "initial_vector"})
_WHATSAPP_IDENTITY_FIELDS = ("wa_id", "whatsapp_id", "whatsapp_user_id", "from", "sender", "phone_number", "msisdn")
_WHATSAPP_IDENTITY_CONTAINERS = ("contact", "contacts", "customer", "user", "metadata")


def _safe_keys(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    return sorted(str(key) for key in payload)


def _error_response(message: str, status_code: int) -> Response:
    return Response(
        content=json.dumps({"error": message}),
        media_type="text/plain",
        status_code=status_code,
    )


def _string_identity(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _extract_whatsapp_authorizer(payload: dict[str, Any]) -> str | None:
    """Extract provider-supplied WhatsApp identity when present.

    The Flow ``data`` object is user-controlled, so it is intentionally not
    inspected for identity binding.
    """
    for field in _WHATSAPP_IDENTITY_FIELDS:
        identity = _string_identity(payload.get(field))
        if identity:
            return identity

    for container_name in _WHATSAPP_IDENTITY_CONTAINERS:
        container = payload.get(container_name)
        containers = container if isinstance(container, list) else [container]
        for item in containers:
            if not isinstance(item, dict):
                continue
            for field in _WHATSAPP_IDENTITY_FIELDS:
                identity = _string_identity(item.get(field))
                if identity:
                    return identity

    return None


def _extract_flow_token(payload: dict[str, Any]) -> str | None:
    return _string_identity(payload.get("flow_token"))


class ProcessedRequest:
    """Container for processed flow request data."""

    def __init__(
        self,
        screen: ScreenType | None,
        data: dict[str, Any],
        flow_token: str | None,
        request_was_encrypted: bool,
        aes_key_bytes: bytes | None = None,
        iv_bytes: bytes | None = None,
        authorizing_channel_user_id: str | None = None,
        action: str | None = None,
        version: str = "3.0",
    ):
        self.screen = screen
        self.data = data
        self.flow_token = flow_token
        self.request_was_encrypted = request_was_encrypted
        self.aes_key_bytes = aes_key_bytes
        self.iv_bytes = iv_bytes
        self.authorizing_channel_user_id = authorizing_channel_user_id
        self.action = action
        self.version = version


async def process_flow_request(req: Request) -> tuple[ProcessedRequest | None, Response | None]:
    """
    Process incoming flow request, handling encryption/decryption.

    Returns:
        Tuple of (ProcessedRequest, Optional[Response])
        Response is only set if there's an error that should be returned immediately
    """
    try:
        body = await req.json()
    except Exception as e:
        logger.warning("whatsapp_flow_invalid_json", error=str(e), exc_info=True)
        return (
            None,
            Response(
                content=json.dumps({"error": "Invalid JSON"}),
                media_type="text/plain",
                status_code=400,
            ),
        )

    encrypted_fields_present = _ENCRYPTED_REQUEST_FIELDS.intersection(body)
    missing_encrypted_fields = _ENCRYPTED_REQUEST_FIELDS.difference(body)
    request_was_encrypted = is_encrypted(body)
    logger.info(
        "whatsapp_flow_request_received",
        payload_keys=_safe_keys(body),
        request_was_encrypted=request_was_encrypted,
        encrypted_fields_present=sorted(encrypted_fields_present),
    )
    if encrypted_fields_present and missing_encrypted_fields:
        logger.warning(
            "whatsapp_flow_encrypted_fields_incomplete",
            payload_keys=_safe_keys(body),
            missing_fields=sorted(missing_encrypted_fields),
        )
        return (
            None,
            _error_response(
                "Encrypted request is missing required fields",
                400,
            ),
        )

    aes_key_bytes = None
    iv_bytes = None
    authorizing_channel_user_id = None

    if settings.whatsapp.require_encrypted_flows and not request_was_encrypted:
        logger.warning("whatsapp_flow_plaintext_rejected", payload_keys=_safe_keys(body))
        return (
            None,
            _error_response(
                "Encrypted request required",
                400,
            ),
        )

    if request_was_encrypted:
        encrypted_data = body["encrypted_flow_data"]
        encrypted_key = body["encrypted_aes_key"]
        iv = body["initial_vector"]

        result = decrypt_flow_data(encrypted_data, encrypted_key, iv)

        if not result:
            logger.warning(
                "whatsapp_flow_decrypt_failed",
                encrypted_data_len=len(str(encrypted_data or "")),
                encrypted_key_len=len(str(encrypted_key or "")),
                iv_len=len(str(iv or "")),
            )
            error_response = {
                "errors": [{"message": ("Failed to decrypt request. Please check your encryption configuration.")}]
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
        screen = _string_identity(decrypted.get("screen"))
        data = decrypted.get("data", {})
        data = data if isinstance(data, dict) else {}
        flow_token = _extract_flow_token(decrypted)
        action = _string_identity(decrypted.get("action"))
        version = _string_identity(decrypted.get("version")) or "3.0"
        authorizing_channel_user_id = _extract_whatsapp_authorizer(decrypted)
        payload_keys = _safe_keys(decrypted)

    else:
        screen = _string_identity(body.get("screen"))
        data = body.get("data", {})
        data = data if isinstance(data, dict) else {}
        flow_token = _extract_flow_token(body)
        action = _string_identity(body.get("action"))
        version = _string_identity(body.get("version")) or "3.0"
        authorizing_channel_user_id = _extract_whatsapp_authorizer(body)
        payload_keys = _safe_keys(body)

    logger.info(
        "whatsapp_flow_request_processed",
        screen=screen,
        action=action,
        version=version,
        request_was_encrypted=request_was_encrypted,
        payload_keys=payload_keys,
        data_keys=_safe_keys(data),
        flow_token_hash=log_fingerprint(flow_token),
        has_authorizing_channel_user_id=bool(authorizing_channel_user_id),
        authorizing_channel_user_id_hash=log_fingerprint(authorizing_channel_user_id),
    )

    return (
        ProcessedRequest(
            screen=screen,
            data=data,
            flow_token=flow_token,
            request_was_encrypted=request_was_encrypted,
            aes_key_bytes=aes_key_bytes,
            iv_bytes=iv_bytes,
            authorizing_channel_user_id=authorizing_channel_user_id,
            action=action,
            version=version,
        ),
        None,
    )
