"""Telegram webhook router — FastAPI endpoint for Telegram Bot updates."""

import hashlib
import hmac
import json
from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.gateway.api.webhooks.telegram.auth import verify_telegram_init_data
from apps.gateway.api.webhooks.telegram.service import TelegramWebhookService
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
from shared.database.connection import get_db
from shared.queue.factory import QueuePublisherFactory
from shared.queue.messages import FlowEvent, FlowEventType
from shared.repositories.user_repository import UserRepository
from shared.services.channel_linking import complete_channel_link_with_pin, is_channel_link_pin_token
from shared.services.onboarding import account_add_service, account_service, bvn_service
from shared.services.telegram_miniapp_bootstrap import consume_telegram_miniapp_bootstrap
from shared.utils.logging import get_logger, log_fingerprint

router = APIRouter(prefix="/webhook", tags=["telegram"])
logger = get_logger(__name__)

_service_instance: TelegramWebhookService | None = None
_TRANSACTION_PIN_FLOW_PREFIXES = frozenset({"transfer", "airtime", "data", "schedule"})


def _parse_typed_pin_flow_token(flow_token: str | None) -> tuple[str, str, str] | None:
    token = str(flow_token or "").strip()
    if "-pin-" not in token:
        return None
    flow_type, remainder = token.split("-pin-", 1)
    if flow_type not in _TRANSACTION_PIN_FLOW_PREFIXES or not remainder:
        return None
    idem_key, separator, token_channel_id = remainder.rpartition("-")
    if not separator or not idem_key or not token_channel_id:
        return None
    return flow_type, idem_key, token_channel_id


def _token_fingerprint(flow_token: str | None) -> str:
    if not flow_token:
        return ""
    return hashlib.sha256(str(flow_token).encode("utf-8")).hexdigest()[:16]


def _telegram_webhook_secret_is_valid(provided_token: str | None) -> bool:
    expected_token = settings.telegram_webhook_secret_token
    if not expected_token:
        return settings.runtime.is_local
    if not provided_token:
        return False
    return hmac.compare_digest(str(provided_token), str(expected_token))


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Handle incoming Telegram Bot webhook updates."""
    if not _telegram_webhook_secret_is_valid(x_telegram_bot_api_secret_token):
        logger.warning(
            "telegram_webhook_unauthorized",
            provided_token_present=bool(x_telegram_bot_api_secret_token),
            provided_token_hash=_token_fingerprint(x_telegram_bot_api_secret_token),
            expected_token_configured=bool(settings.telegram_webhook_secret_token),
            msg="Invalid or missing secret token",
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        update = await request.json()
        user_repo = UserRepository(db)
        publisher = QueuePublisherFactory.get_publisher()
        service = TelegramWebhookService(publisher=publisher, user_repository=user_repo)
        handled = await service.process_update(update)
        logger.info(
            "telegram_webhook_processed",
            update_id=update.get("update_id"),
            handled=handled,
        )
        await db.commit()
        return Response(status_code=200)
    except Exception as e:
        await db.rollback()
        logger.error(
            "telegram_webhook_error_acknowledged",
            error=str(e),
            error_type=type(e).__name__,
            retry_suppressed=True,
            acknowledged=True,
            delivery_policy="drop_on_failure_no_retry",
            exc_info=True,
        )
        return Response(status_code=200)


class BvnInput(BaseModel):
    flow_token: str
    bvn: str


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

    result = await bvn_service.initiate_bvn_verification(data.flow_token, data.bvn)
    return result


class MethodInput(BaseModel):
    flow_token: str
    method: str


class LinkingSessionInput(BaseModel):
    flow_token: str


def _invalid_linking_session_error() -> dict:
    return {"success": False, "error": "Invalid linking session. Please start account linking again."}


def _expired_linking_session_error() -> dict:
    return {"success": False, "error": "Linking session expired. Please start account linking again."}


def _no_linking_methods_error() -> dict:
    return {"success": False, "error": "No verification methods found. Please restart account linking."}


def _invalid_telegram_session_error() -> dict:
    return {"success": False, "error": "Invalid or expired session. Please reopen this page from Telegram."}


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
    init_user_id = _telegram_init_user_id(user_data)
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
            flow_token_hash=_token_fingerprint(token),
            error_type=type(e).__name__,
        )
        return None, _invalid_telegram_session_error()

    if not session:
        logger.warning("telegram_onboarding_session_miss", flow_token_hash=_token_fingerprint(token))
        return None, _invalid_telegram_session_error()

    if not _telegram_session_owner_matches(user_data, session):
        logger.warning(
            "telegram_onboarding_session_owner_mismatch",
            flow_token_hash=_token_fingerprint(token),
            init_user_id_hash=_token_fingerprint(_telegram_init_user_id(user_data)),
            has_session_owner=bool(_session_telegram_owner_ids(session, require_telegram_channel=False)),
            channel=session.get("channel"),
            step=session.get("step"),
        )
        return None, _invalid_telegram_session_error()

    return session, None


async def _get_valid_linking_session(flow_token: str, user_data: dict | None = None) -> tuple[dict | None, dict | None]:
    token = (flow_token or "").strip()
    if not token or not token.startswith("link-"):
        logger.warning("telegram_linking_session_invalid_token", flow_token_hash=_token_fingerprint(flow_token))
        return None, _invalid_linking_session_error()

    session_result = await bvn_service.get_session_status(token)
    if session_result.backend_error:
        logger.error(
            "telegram_linking_session_backend_error",
            flow_token_hash=_token_fingerprint(token),
            error=session_result.error,
        )
        return None, _expired_linking_session_error()

    session = session_result.data
    if not session_result.found or not session:
        logger.warning("telegram_linking_session_miss", flow_token_hash=_token_fingerprint(token))
        return None, _expired_linking_session_error()

    if not bool(session.get("is_account_linking")):
        logger.warning(
            "telegram_linking_session_invalid_state",
            flow_token_hash=_token_fingerprint(token),
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
            flow_token_hash=_token_fingerprint(token),
            init_user_id_hash=_token_fingerprint(_telegram_init_user_id(user_data)),
            has_session_owner=bool(_session_telegram_owner_ids(session, require_telegram_channel=True)),
            channel=session.get("channel"),
            step=session.get("step"),
        )
        return None, _invalid_telegram_session_error()

    logger.info(
        "telegram_linking_session_validated",
        flow_token_hash=_token_fingerprint(token),
        step=session.get("step"),
    )
    return session, None


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

    result = await bvn_service.send_otp(data.flow_token, data.method)
    return result


class OtpInput(BaseModel):
    flow_token: str
    otp: str


class LinkingMethodInput(BaseModel):
    flow_token: str
    method: str | None = None


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


class LinkingOtpInput(BaseModel):
    flow_token: str
    otp: str


@router.post("/telegram/linking/otp")
async def telegram_linking_otp(data: LinkingOtpInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle OTP verification step for Telegram relinking flow."""
    _, error = await _get_valid_linking_session(data.flow_token, user_data)
    if error:
        return error
    return await bvn_service.verify_otp(data.flow_token, data.otp)


class LinkingAccountInput(BaseModel):
    flow_token: str
    account_id: str


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

    result = await bvn_service.verify_otp(data.flow_token, data.otp)
    return result


class AccountInput(BaseModel):
    flow_token: str
    account_id: str


@router.post("/telegram/onboarding/account")
async def telegram_onboarding_account(data: AccountInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle Account selection for Telegram Onboarding."""
    _, owner_error = await _get_owned_onboarding_session(data.flow_token, user_data)
    if owner_error:
        return owner_error

    result = await account_service.select_account(data.flow_token, data.account_id)
    return result


class CompleteInput(BaseModel):
    flow_token: str
    pin: str
    email: str
    address: str


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
            from shared.services.onboarding import session_manager

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


class PinSubmitInput(BaseModel):
    flow_token: str
    pin: str
    chat_id: str = ""
    message_id: str = ""


def _telegram_init_user_id(user_data: dict) -> str:
    raw_user = user_data.get("user")
    if isinstance(raw_user, str):
        try:
            parsed = json.loads(raw_user)
        except json.JSONDecodeError:
            return ""
        return str(parsed.get("id") or "")
    if isinstance(raw_user, dict):
        return str(raw_user.get("id") or "")
    return ""


class TelegramBootstrapInput(BaseModel):
    boot: str
    endpoint: str


@router.post("/telegram/bootstrap")
async def telegram_bootstrap(
    data: TelegramBootstrapInput,
    user_data: dict = Depends(verify_telegram_init_data),
) -> dict:
    """Exchange a short-lived Mini App bootstrap nonce for the server-side flow token."""
    init_user_id = _telegram_init_user_id(user_data)
    if not init_user_id:
        return _invalid_telegram_session_error()

    try:
        bootstrap = await consume_telegram_miniapp_bootstrap(
            nonce=data.boot,
            endpoint=data.endpoint,
            init_user_id=init_user_id,
        )
    except ValueError:
        logger.warning("telegram_miniapp_bootstrap_invalid_endpoint", endpoint=data.endpoint)
        return _invalid_telegram_session_error()

    if not bootstrap:
        return _invalid_telegram_session_error()

    response: dict[str, Any] = {
        "success": True,
        "flow_token": bootstrap.flow_token,
        "chat_id": bootstrap.chat_id,
    }
    response.update(bootstrap.extra)
    return response


@router.post("/telegram/pin_submit")
async def telegram_pin_submit(
    data: PinSubmitInput, user_data: dict = Depends(verify_telegram_init_data), db: AsyncSession = Depends(get_db)
) -> dict:
    """Handle direct PIN submission from pin_entry.html Mini App.

    Mirrors WhatsApp's transaction_pin_handler logic:
    1. Parse flow_token to extract idempotency_key
    2. Verify PIN via AuthorizationService (hash check + max 3 attempts)
    3. Only publish FlowEvent on success
    4. Return error details on failure so Mini App can show them
    """
    if not data.flow_token or not data.pin:
        return {"success": False, "error": "Missing PIN or token"}

    init_user_id = _telegram_init_user_id(user_data)
    if not init_user_id:
        return {"success": False, "error": "Telegram authentication missing. Reopen this page from Telegram."}
    if not data.chat_id or data.chat_id != init_user_id:
        logger.warning(
            "telegram_pin_submit_chat_mismatch",
            chat_id_hash=_token_fingerprint(data.chat_id),
            init_user_id_hash=_token_fingerprint(init_user_id),
        )
        return {"success": False, "error": "This PIN request is not valid for this Telegram account."}

    if is_channel_link_pin_token(data.flow_token):
        result = await complete_channel_link_with_pin(
            flow_token=data.flow_token,
            pin=data.pin,
            authorizing_channel="telegram",
            authorizing_channel_user_id=init_user_id,
        )
        if not result.success:
            return {
                "success": False,
                "error": result.error or "PIN verification failed",
                "attempts_remaining": result.attempts_remaining,
                "locked": result.locked,
            }

        if result.requested_channel == "whatsapp":
            try:
                await WhatsAppClient().send_text(
                    to=result.requested_channel_user_id,
                    text="Your WhatsApp number has been linked. You can now use banking features there.",
                    suppress_typing_indicator=True,
                )
            except Exception as e:
                logger.warning("channel_link_requested_channel_notify_failed", channel="whatsapp", error=str(e))

        try:
            from shared.cache.redis_client import RedisClient

            stored_msg_id = await RedisClient.get_client().getdel(f"tg:pin_msg:{data.flow_token}")
            if stored_msg_id:
                await TelegramClient().mark_as_authorized(init_user_id, stored_msg_id)
        except Exception as e:
            logger.warning("telegram_channel_link_pin_keyboard_removal_failed", error=str(e))

        return {"success": True}

    parsed_pin_token = _parse_typed_pin_flow_token(data.flow_token)
    if not parsed_pin_token:
        return {"success": False, "error": "Session expired. Please start a new transaction."}

    flow_type, idem_key, token_channel_id = parsed_pin_token
    if token_channel_id != init_user_id:
        logger.warning(
            "telegram_pin_submit_token_owner_mismatch",
            flow_token_hash=_token_fingerprint(data.flow_token),
            token_channel_id_hash=_token_fingerprint(token_channel_id),
            init_user_id_hash=_token_fingerprint(init_user_id),
        )
        return {"success": False, "error": "This PIN request is not valid for this Telegram account."}

    # --- Look up the real phone_number from Redis (chat_id != phone for Telegram) ---
    from shared.cache.redis_client import RedisClient
    from shared.services.auth.authorization import AuthorizationService

    redis_client = RedisClient.get_client()

    # Resolve real phone number from transaction token stored during flow creation
    phone_number: str | None = None
    phone_number = await redis_client.get(f"{flow_type}:token:{idem_key}:phone")

    if not phone_number:
        return {"success": False, "error": "Session expired. Please start a new transaction."}

    safe_phone_number = str(phone_number)

    auth_service = AuthorizationService(redis_client=redis_client)
    auth_result = await auth_service.verify_pin(
        phone_number=safe_phone_number,
        pin=str(data.pin),
        idempotency_key=idem_key,
        transaction_type=flow_type,
    )

    if not auth_result.transaction_type:
        auth_result.transaction_type = flow_type

    await auth_service.store_pin_verification_result(idem_key, auth_result)

    if not auth_result.verified:
        logger.info(
            "telegram_pin_verification_failed",
            chat_id_hash=_token_fingerprint(data.chat_id),
            error=auth_result.error,
        )
        return {
            "success": False,
            "error": auth_result.error or "PIN verification failed",
            "attempts_remaining": auth_result.attempts_remaining,
            "locked": auth_result.attempts_remaining <= 0,
        }

    resolved_flow_type = auth_result.transaction_type or flow_type

    event = FlowEvent(
        event_type=FlowEventType.PIN_VERIFIED,
        phone_number=safe_phone_number,
        flow_type=resolved_flow_type,
        idempotency_key=idem_key,
        success=True,
        channel="telegram",
        extra_data={"source": "telegram_mini_app_rest", "chat_id": data.chat_id},
    )

    try:
        publisher = QueuePublisherFactory.get_publisher()
        await publisher.publish(
            topic="flow_event.process",
            message=cast(dict, event.to_dict()),
        )
        logger.info(
            "telegram_pin_rest_published",
            chat_id_hash=_token_fingerprint(data.chat_id),
            flow_type=resolved_flow_type,
        )

        if data.chat_id:
            try:
                stored_msg_id = await redis_client.getdel(f"tg:pin_msg:{data.flow_token}")
                if stored_msg_id:
                    _telegram = TelegramClient()
                    await _telegram.mark_as_authorized(data.chat_id, stored_msg_id)
                    logger.info(
                        "telegram_pin_keyboard_marked_authorized",
                        chat_id_hash=_token_fingerprint(data.chat_id),
                        message_id_hash=_token_fingerprint(stored_msg_id),
                    )
            except Exception as kb_err:
                logger.warning("telegram_pin_keyboard_removal_failed", error=str(kb_err))

        return {"success": True}
    except Exception as e:
        logger.error("telegram_pin_rest_failed", error=str(e), exc_info=True)
        return {"success": False, "error": "Internal server error"}
