"""WhatsApp Flow session ownership helpers."""

from dataclasses import dataclass
from typing import Any

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import format_error_response
from banking.accounts.onboarding.runtime import session_manager
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

GENERIC_FLOW_SESSION_ERROR = "Invalid or expired session. Please start again."


@dataclass(frozen=True, slots=True)
class FlowOwnerCheck:
    """Result of binding a provider identity to a flow session."""

    ok: bool
    session: dict[str, Any] | None = None
    reason: str = ""


def normalize_whatsapp_identity(value: Any) -> str:
    """Normalize WhatsApp IDs and Nigerian phone values to comparable digits."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("234") and len(digits) == 13:
        return "0" + digits[3:]
    return digits


def whatsapp_identity_matches(authorizing_channel_user_id: str | None, expected_value: Any) -> bool:
    """Return whether a provider WhatsApp identity matches an expected owner value."""
    actual = normalize_whatsapp_identity(authorizing_channel_user_id)
    expected = normalize_whatsapp_identity(expected_value)
    return bool(actual and expected and actual == expected)


def provider_identity_is_required() -> bool:
    """Return whether this runtime must receive provider-bound WhatsApp identity."""
    return not settings.runtime.is_local


def has_required_provider_identity(authorizing_channel_user_id: str | None) -> bool:
    """Return whether provider identity requirements are satisfied."""
    return bool(authorizing_channel_user_id) or not provider_identity_is_required()


async def verify_whatsapp_flow_session_owner(
    *,
    flow_token: str,
    authorizing_channel_user_id: str | None,
    screen: str,
) -> FlowOwnerCheck:
    """Verify the WhatsApp provider identity owns an onboarding/linking flow session."""
    if not flow_token:
        return FlowOwnerCheck(ok=False, reason="missing_flow_token")

    if not authorizing_channel_user_id and provider_identity_is_required():
        logger.warning(
            "whatsapp_flow_missing_authorizer",
            screen=screen,
            flow_token_hash=log_fingerprint(flow_token),
        )
        return FlowOwnerCheck(ok=False, reason="missing_provider_identity")

    read_result = await session_manager.read_session(flow_token)
    session = read_result.data or {}
    if not read_result.found:
        logger.warning(
            "whatsapp_flow_session_owner_missing_session",
            screen=screen,
            flow_token_hash=log_fingerprint(flow_token),
        )
        return FlowOwnerCheck(ok=False, reason="missing_session")

    if not authorizing_channel_user_id and not provider_identity_is_required():
        return FlowOwnerCheck(ok=True, session=session)

    owner_candidates = [
        session.get("channel_user_id"),
        session.get("phone_number"),
    ]
    if any(whatsapp_identity_matches(authorizing_channel_user_id, candidate) for candidate in owner_candidates):
        return FlowOwnerCheck(ok=True, session=session)

    logger.warning(
        "whatsapp_flow_session_owner_mismatch",
        screen=screen,
        flow_token_hash=log_fingerprint(flow_token),
        authorizer_hash=log_fingerprint(normalize_whatsapp_identity(authorizing_channel_user_id)),
        channel_user_id_hash=log_fingerprint(normalize_whatsapp_identity(session.get("channel_user_id"))),
        phone_hash=log_fingerprint(normalize_whatsapp_identity(session.get("phone_number"))),
        session_channel=session.get("channel"),
    )
    return FlowOwnerCheck(ok=False, session=session, reason="owner_mismatch")


def format_owner_error_response(
    screen: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
):
    """Return the generic Flow error used for failed ownership checks."""
    return format_error_response(
        screen,
        GENERIC_FLOW_SESSION_ERROR,
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
    )
