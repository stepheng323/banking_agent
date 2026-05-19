"""Shared service for PIN-authorized cross-channel identity linking."""

from dataclasses import dataclass
from typing import Any, Literal

from shared.cache.channel_identity_cache import store_channel_identity_user
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.auth import AuthorizationService
from shared.services.onboarding import session_manager as default_session_manager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CHANNEL_LINK_PIN_PREFIX = "channel-link-pin-"
CHANNEL_LINK_TRANSACTION_TYPE = "channel_link"
CHANNEL_LINK_SESSION_PURPOSE = "channel_identity_link"

ChannelLinkStatus = Literal[
    "success",
    "expired",
    "invalid_token",
    "invalid_session",
    "wrong_authorizer",
    "invalid_pin",
    "identity_claimed",
    "failed",
]


@dataclass(slots=True)
class ChannelLinkPinResult:
    success: bool
    status: ChannelLinkStatus
    error: str = ""
    attempts_remaining: int = 3
    locked: bool = False
    requested_channel: str = ""
    requested_channel_user_id: str = ""
    user: Any | None = None


def build_channel_link_pin_token(channel_link_session_token: str) -> str:
    return f"{CHANNEL_LINK_PIN_PREFIX}{channel_link_session_token}"


def parse_channel_link_pin_token(flow_token: str | None) -> str | None:
    token = str(flow_token or "")
    if not token.startswith(CHANNEL_LINK_PIN_PREFIX):
        return None
    session_token = token.removeprefix(CHANNEL_LINK_PIN_PREFIX)
    return session_token or None


def is_channel_link_pin_token(flow_token: str | None) -> bool:
    return parse_channel_link_pin_token(flow_token) is not None


def _normalize_identity(channel: str, value: str | None) -> str:
    raw = str(value or "").strip()
    if channel != "whatsapp":
        return raw

    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("234") and len(digits) == 13:
        return f"0{digits[3:]}"
    return digits


async def complete_channel_link_with_pin(
    *,
    flow_token: str,
    pin: str,
    authorizing_channel: str,
    authorizing_channel_user_id: str | None = None,
    session_manager: Any = default_session_manager,
    authorization_service: AuthorizationService | None = None,
) -> ChannelLinkPinResult:
    """Verify PIN and complete a pending channel link request."""
    session_token = parse_channel_link_pin_token(flow_token)
    if not session_token:
        return ChannelLinkPinResult(
            success=False,
            status="invalid_token",
            error="Invalid link authorization token.",
        )

    session = await session_manager.get_session(session_token)
    if not session or session.get("purpose") != CHANNEL_LINK_SESSION_PURPOSE:
        return ChannelLinkPinResult(
            success=False,
            status="expired",
            error="This link request has expired. Please start again.",
        )

    requested_channel = str(session.get("requested_channel") or "")
    requested_identity = str(session.get("requested_channel_user_id") or "")
    expected_authorizing_channel = str(session.get("authorizing_channel") or "")
    expected_authorizing_identity = str(session.get("authorizing_channel_user_id") or "")
    phone_number = str(session.get("phone_number") or "")
    user_id = str(session.get("user_id") or "")
    step = str(session.get("step") or "")

    base_result = ChannelLinkPinResult(
        success=False,
        status="invalid_session",
        requested_channel=requested_channel,
        requested_channel_user_id=requested_identity,
    )
    if (
        not user_id
        or not phone_number
        or not requested_channel
        or not requested_identity
        or not expected_authorizing_channel
        or not expected_authorizing_identity
        or step != "pending_existing_channel_authorization"
    ):
        await session_manager.delete_session(session_token)
        base_result.error = "This link request is invalid. Please start again."
        return base_result

    if expected_authorizing_channel != authorizing_channel:
        logger.warning(
            "channel_link_pin_wrong_authorizing_channel",
            expected_channel=expected_authorizing_channel,
            actual_channel=authorizing_channel,
            requested_channel=requested_channel,
        )
        return ChannelLinkPinResult(
            success=False,
            status="wrong_authorizer",
            error="This link request is not valid for this channel.",
            requested_channel=requested_channel,
            requested_channel_user_id=requested_identity,
        )

    if authorizing_channel_user_id is not None:
        expected = _normalize_identity(authorizing_channel, expected_authorizing_identity)
        actual = _normalize_identity(authorizing_channel, authorizing_channel_user_id)
        if expected != actual:
            logger.warning(
                "channel_link_pin_wrong_authorizing_identity",
                authorizing_channel=authorizing_channel,
                requested_channel=requested_channel,
            )
            return ChannelLinkPinResult(
                success=False,
                status="wrong_authorizer",
                error="This link request is not valid for this account.",
                requested_channel=requested_channel,
                requested_channel_user_id=requested_identity,
            )

    auth_service = authorization_service or AuthorizationService()
    auth_result = await auth_service.verify_pin(
        phone_number=phone_number,
        pin=str(pin),
        idempotency_key=session_token,
        transaction_type=CHANNEL_LINK_TRANSACTION_TYPE,
    )
    await auth_service.store_pin_verification_result(session_token, auth_result)

    if not auth_result.verified:
        return ChannelLinkPinResult(
            success=False,
            status="invalid_pin",
            error=auth_result.error or "PIN verification failed.",
            attempts_remaining=auth_result.attempts_remaining,
            locked=auth_result.attempts_remaining <= 0,
            requested_channel=requested_channel,
            requested_channel_user_id=requested_identity,
        )

    if auth_result.user_id and str(auth_result.user_id) != user_id:
        logger.warning(
            "channel_link_pin_user_mismatch",
            requested_channel=requested_channel,
        )
        return ChannelLinkPinResult(
            success=False,
            status="wrong_authorizer",
            error="This PIN does not match the account being linked.",
            requested_channel=requested_channel,
            requested_channel_user_id=requested_identity,
        )

    try:
        async with UnitOfWork() as uow:
            if not uow.users:
                raise RuntimeError("user repository unavailable")

            user = await uow.users.get_by_id(user_id)
            if not user:
                raise RuntimeError("user not found for channel link")
            if str(getattr(user, "phone_number", "") or "") != phone_number:
                raise RuntimeError("channel link phone mismatch")

            if expected_authorizing_channel != "whatsapp":
                authorizing_user = await uow.users.get_by_channel_identity(
                    expected_authorizing_channel,
                    expected_authorizing_identity,
                )
                if not authorizing_user or str(authorizing_user.id) != user_id:
                    logger.warning(
                        "channel_link_authorizing_identity_not_linked",
                        authorizing_channel=expected_authorizing_channel,
                        requested_channel=requested_channel,
                    )
                    return ChannelLinkPinResult(
                        success=False,
                        status="wrong_authorizer",
                        error="This link request is no longer valid for this account.",
                        requested_channel=requested_channel,
                        requested_channel_user_id=requested_identity,
                    )

            existing_user = await uow.users.get_by_channel_identity(requested_channel, requested_identity)
            if existing_user and str(existing_user.id) != user_id:
                logger.warning("channel_link_identity_already_claimed", requested_channel=requested_channel)
                return ChannelLinkPinResult(
                    success=False,
                    status="identity_claimed",
                    error="That channel is already linked to another profile.",
                    requested_channel=requested_channel,
                    requested_channel_user_id=requested_identity,
                )

            if not existing_user:
                await uow.users.link_channel_identity(user_id, requested_channel, requested_identity)

        await session_manager.delete_session(session_token)
        await store_channel_identity_user(requested_channel, requested_identity, user)
        return ChannelLinkPinResult(
            success=True,
            status="success",
            requested_channel=requested_channel,
            requested_channel_user_id=requested_identity,
            user=user,
        )
    except Exception as e:
        logger.error("channel_link_pin_completion_failed", error=str(e), requested_channel=requested_channel)
        return ChannelLinkPinResult(
            success=False,
            status="failed",
            error="I couldn't complete that link request. Please try again.",
            requested_channel=requested_channel,
            requested_channel_user_id=requested_identity,
        )
