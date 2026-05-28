"""Telegram Mini App onboarding and account relinking routes."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.gateway.api.webhooks.telegram.auth import verify_telegram_init_data
from apps.gateway.api.webhooks.telegram.session import (
    invalid_telegram_session_error,
    telegram_init_user_id,
    token_fingerprint,
)
from shared.clients.telegram.client import TelegramClient
from shared.services.onboarding.runtime import account_add_service, account_service, bvn_service
from shared.utils.logging import get_logger, log_fingerprint

router = APIRouter(tags=["telegram"])
logger = get_logger(__name__)


class BvnInput(BaseModel):
    flow_token: str
    bvn: str


class MethodInput(BaseModel):
    flow_token: str
    method: str


class LinkingSessionInput(BaseModel):
    flow_token: str


class OtpInput(BaseModel):
    flow_token: str
    otp: str


class LinkingMethodInput(BaseModel):
    flow_token: str
    method: str | None = None


class LinkingOtpInput(BaseModel):
    flow_token: str
    otp: str


class LinkingAccountInput(BaseModel):
    flow_token: str
    account_id: str


class AccountInput(BaseModel):
    flow_token: str
    account_id: str


class CompleteInput(BaseModel):
    flow_token: str
    pin: str
    email: str
    address: str


def _invalid_linking_session_error() -> dict[str, Any]:
    return {"success": False, "error": "Invalid linking session. Please start account linking again."}


def _expired_linking_session_error() -> dict[str, Any]:
    return {"success": False, "error": "Linking session expired. Please start account linking again."}


def _no_linking_methods_error() -> dict[str, Any]:
    return {"success": False, "error": "No verification methods found. Please restart account linking."}


def _string_identity(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _session_telegram_owner_ids(session: dict | None, *, require_telegram_channel: bool) -> list[str]:
    if not isinstance(session, dict):
        return []

    channel = _string_identity(session.get("channel")).lower()
    if require_telegram_channel and channel and channel != "telegram":
        return []

    owner_ids: list[str] = []
    for field in ("channel_user_id", "cta_chat_id"):
        owner_id = _string_identity(session.get(field))
        if owner_id and owner_id not in owner_ids:
            owner_ids.append(owner_id)
    return owner_ids


def _telegram_session_owner_matches(
    user_data: dict,
    session: dict | None,
    *,
    require_telegram_channel: bool = False,
) -> bool:
    init_user_id = telegram_init_user_id(user_data)
    if not init_user_id:
        return False
    return init_user_id in _session_telegram_owner_ids(session, require_telegram_channel=require_telegram_channel)


async def _get_owned_onboarding_session(flow_token: str, user_data: dict) -> tuple[dict | None, dict | None]:
    token = (flow_token or "").strip()
    try:
        session = await bvn_service.get_session_data(token)
    except Exception as e:
        logger.warning(
            "telegram_onboarding_session_read_failed",
            flow_token_hash=token_fingerprint(token),
            error_type=type(e).__name__,
        )
        return None, invalid_telegram_session_error()

    if not session:
        logger.warning("telegram_onboarding_session_miss", flow_token_hash=token_fingerprint(token))
        return None, invalid_telegram_session_error()

    if not _telegram_session_owner_matches(user_data, session):
        logger.warning(
            "telegram_onboarding_session_owner_mismatch",
            flow_token_hash=token_fingerprint(token),
            init_user_id_hash=token_fingerprint(telegram_init_user_id(user_data)),
            has_session_owner=bool(_session_telegram_owner_ids(session, require_telegram_channel=False)),
            channel=session.get("channel"),
            step=session.get("step"),
        )
        return None, invalid_telegram_session_error()

    return session, None


async def _get_valid_linking_session(flow_token: str, user_data: dict | None = None) -> tuple[dict | None, dict | None]:
    token = (flow_token or "").strip()
    if not token or not token.startswith("link-"):
        logger.warning("telegram_linking_session_invalid_token", flow_token_hash=token_fingerprint(flow_token))
        return None, _invalid_linking_session_error()

    session_result = await bvn_service.get_session_status(token)
    if session_result.backend_error:
        logger.error(
            "telegram_linking_session_backend_error",
            flow_token_hash=token_fingerprint(token),
            error=session_result.error,
        )
        return None, _expired_linking_session_error()

    session = session_result.data
    if not session_result.found or not session:
        logger.warning("telegram_linking_session_miss", flow_token_hash=token_fingerprint(token))
        return None, _expired_linking_session_error()

    if not bool(session.get("is_account_linking")):
        logger.warning(
            "telegram_linking_session_invalid_state",
            flow_token_hash=token_fingerprint(token),
            step=session.get("step"),
        )
        return None, _invalid_linking_session_error()

    if user_data is not None and not _telegram_session_owner_matches(
        user_data,
        session,
        require_telegram_channel=True,
    ):
        logger.warning(
            "telegram_linking_session_owner_mismatch",
            flow_token_hash=token_fingerprint(token),
            init_user_id_hash=token_fingerprint(telegram_init_user_id(user_data)),
            has_session_owner=bool(_session_telegram_owner_ids(session, require_telegram_channel=True)),
            channel=session.get("channel"),
            step=session.get("step"),
        )
        return None, invalid_telegram_session_error()

    logger.info(
        "telegram_linking_session_validated",
        flow_token_hash=token_fingerprint(token),
        step=session.get("step"),
    )
    return session, None


@router.post("/telegram/onboarding/bvn")
async def telegram_onboarding_bvn(data: BvnInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle BVN verification for Telegram Onboarding."""
    session, owner_error = await _get_owned_onboarding_session(data.flow_token, user_data)
    if owner_error:
        return owner_error

    try:
        phone_number = session.get("phone_number", "") if session else ""
        if phone_number:
            from shared.database.enums import UserOnboardingStatusEnum
            from shared.repositories.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                if uow.users:
                    user = await uow.users.get_by_phone(phone_number)
                    if (
                        user
                        and getattr(user, "onboarding_status", None)
                        == UserOnboardingStatusEnum.ONBOARDING_COMPLETED.value
                    ):
                        logger.info("onboarding_already_completed", phone_hash=log_fingerprint(phone_number))
                        return {
                            "success": False,
                            "error": "You have already completed onboarding. "
                            "Please continue using the bot to make transactions.",
                        }
    except Exception as e:
        logger.warning("onboarding_guard_check_failed", error=str(e))

    return await bvn_service.initiate_bvn_verification(data.flow_token, data.bvn)


@router.post("/telegram/onboarding/linking_session")
async def telegram_onboarding_linking_session(
    data: LinkingSessionInput, user_data: dict = Depends(verify_telegram_init_data)
) -> dict:
    """Fetch pre-seeded account relinking session data for Telegram mini app."""
    session, error = await _get_valid_linking_session(data.flow_token, user_data)
    if error:
        return error

    methods = session.get("methods", [])
    if not isinstance(methods, list) or not methods:
        return _no_linking_methods_error()

    return {
        "success": True,
        "data": {
            "bvn": session.get("bvn", ""),
            "methods": methods,
        },
    }


@router.post("/telegram/onboarding/send_otp")
async def telegram_send_otp(data: MethodInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Send OTP for Telegram Onboarding."""
    _, owner_error = await _get_owned_onboarding_session(data.flow_token, user_data)
    if owner_error:
        return owner_error

    return await bvn_service.send_otp(data.flow_token, data.method)


@router.post("/telegram/linking/method")
async def telegram_linking_method(
    data: LinkingMethodInput, user_data: dict = Depends(verify_telegram_init_data)
) -> dict:
    """Handle METHOD_SELECTION step for Telegram relinking flow."""
    session, error = await _get_valid_linking_session(data.flow_token, user_data)
    if error:
        return error

    if not data.method:
        methods = session.get("methods", [])
        if not isinstance(methods, list) or not methods:
            return _no_linking_methods_error()
        return {
            "success": True,
            "data": {
                "bvn": session.get("bvn", ""),
                "methods": methods,
            },
        }

    return await bvn_service.send_otp(data.flow_token, data.method)


@router.post("/telegram/linking/otp")
async def telegram_linking_otp(data: LinkingOtpInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle OTP verification step for Telegram relinking flow."""
    _, error = await _get_valid_linking_session(data.flow_token, user_data)
    if error:
        return error
    return await bvn_service.verify_otp(data.flow_token, data.otp)


@router.post("/telegram/linking/account")
async def telegram_linking_account(
    data: LinkingAccountInput, user_data: dict = Depends(verify_telegram_init_data)
) -> dict:
    """Handle account selection step for Telegram relinking flow."""
    _, error = await _get_valid_linking_session(data.flow_token, user_data)
    if error:
        return error
    return await account_add_service.add_account(data.flow_token, data.account_id)


@router.post("/telegram/onboarding/otp")
async def telegram_onboarding_otp(data: OtpInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle OTP verification for Telegram Onboarding."""
    _, owner_error = await _get_owned_onboarding_session(data.flow_token, user_data)
    if owner_error:
        return owner_error

    return await bvn_service.verify_otp(data.flow_token, data.otp)


@router.post("/telegram/onboarding/account")
async def telegram_onboarding_account(data: AccountInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle Account selection for Telegram Onboarding."""
    _, owner_error = await _get_owned_onboarding_session(data.flow_token, user_data)
    if owner_error:
        return owner_error

    return await account_service.select_account(data.flow_token, data.account_id)


@router.post("/telegram/onboarding/complete")
async def telegram_onboarding_complete(
    data: CompleteInput, user_data: dict = Depends(verify_telegram_init_data)
) -> dict:
    """Handle Onboarding completion for Telegram Onboarding."""
    _, owner_error = await _get_owned_onboarding_session(data.flow_token, user_data)
    if owner_error:
        return owner_error

    result = await account_service.complete_onboarding(
        data.flow_token,
        pin=data.pin,
        email=data.email,
        address=data.address,
        channel="telegram",
    )

    if result.get("success"):
        try:
            from shared.services.onboarding.runtime import session_manager

            read_result = await session_manager.read_session(data.flow_token)
            session = read_result.data or {}
            cta_message_id = session.get("cta_message_id")
            cta_chat_id = session.get("cta_chat_id")

            if cta_message_id and cta_chat_id:
                telegram_client = TelegramClient()
                await telegram_client._call(
                    "editMessageReplyMarkup",
                    {
                        "chat_id": cta_chat_id,
                        "message_id": int(cta_message_id),
                        "reply_markup": {"inline_keyboard": [[{"text": "✓ Setup Complete", "callback_data": "noop"}]]},
                    },
                )
        except Exception as e:
            logger.warning("cta_disable_failed", error=str(e))

    return result
