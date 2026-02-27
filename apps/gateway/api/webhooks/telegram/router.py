"""Telegram webhook router — FastAPI endpoint for Telegram Bot updates."""

from typing import cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.gateway.api.webhooks.telegram.auth import verify_telegram_init_data
from apps.gateway.api.webhooks.telegram.service import TelegramWebhookService
from shared.clients.telegram.client import TelegramClient
from shared.config.settings import settings
from shared.database.connection import get_db
from shared.queue.factory import QueuePublisherFactory
from shared.queue.messages import FlowEvent, FlowEventType
from shared.repositories.user_repository import UserRepository
from shared.services.onboarding import account_service, bvn_service
from shared.utils.logging import get_logger

router = APIRouter(prefix="/webhook", tags=["telegram"])
logger = get_logger(__name__)

_service_instance: TelegramWebhookService | None = None


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Handle incoming Telegram Bot webhook updates."""
    # 1. Validate Secret Token
    expected_token = settings.telegram_webhook_secret_token
    if expected_token and x_telegram_bot_api_secret_token != expected_token:
        logger.warning(
            "telegram_webhook_unauthorized",
            provided_token=x_telegram_bot_api_secret_token,
            msg="Invalid or missing secret token",
        )
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        update = await request.json()
        user_repo = UserRepository(db)
        publisher = QueuePublisherFactory.get_publisher()
        service = TelegramWebhookService(
            publisher=publisher, user_repository=user_repo, telegram_client=TelegramClient()
        )
        await service.process_update(update)
        # Commit manually if any inserts (like linking child accounts) happened inside process_update
        await db.commit()
        return Response(status_code=200)
    except Exception as e:
        await db.rollback()
        logger.error("telegram_webhook_error", error=str(e), exc_info=True)
        return Response(status_code=200)


class BvnInput(BaseModel):
    flow_token: str
    bvn: str


@router.post("/telegram/onboarding/bvn")
async def telegram_onboarding_bvn(data: BvnInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle BVN verification for Telegram Onboarding."""
    try:
        session = await bvn_service.get_session_data(data.flow_token)
        phone_number = (session or {}).get("phone_number", "")
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
                        logger.info("onboarding_already_completed", phone=phone_number)
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


@router.post("/telegram/onboarding/send_otp")
async def telegram_send_otp(data: MethodInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Send OTP for Telegram Onboarding."""
    result = await bvn_service.send_otp(data.flow_token, data.method)
    return result


class OtpInput(BaseModel):
    flow_token: str
    otp: str


@router.post("/telegram/onboarding/otp")
async def telegram_onboarding_otp(data: OtpInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle OTP verification for Telegram Onboarding."""
    result = await bvn_service.verify_otp(data.flow_token, data.otp)
    return result


class AccountInput(BaseModel):
    flow_token: str
    account_id: str


@router.post("/telegram/onboarding/account")
async def telegram_onboarding_account(data: AccountInput, user_data: dict = Depends(verify_telegram_init_data)) -> dict:
    """Handle Account selection for Telegram Onboarding."""
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

            session = await session_manager.get_session(data.flow_token)
            cta_message_id = (session or {}).get("cta_message_id")
            cta_chat_id = (session or {}).get("cta_chat_id")

            if cta_message_id and cta_chat_id:
                telegram_client = TelegramClient()
                await telegram_client._call(
                    "editMessageReplyMarkup",
                    {
                        "chat_id": cta_chat_id,
                        "message_id": int(cta_message_id),
                        "reply_markup": {"inline_keyboard": [[{"text": "✅ Setup Complete", "callback_data": "noop"}]]},
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


@router.post("/telegram/pin_submit")
async def telegram_pin_submit(
    data: PinSubmitInput, user_data: dict = Depends(verify_telegram_init_data), db: AsyncSession = Depends(get_db)
) -> dict:
    """Handle direct PIN submission from pin_entry.html Mini App.

    Mirrors WhatsApp's transaction_pin_handler logic:
    1. Parse flow_token to extract phone_number and idempotency_key
    2. Verify PIN via AuthorizationService (hash check + max 3 attempts)
    3. Only publish FlowEvent on success
    4. Return error details on failure so Mini App can show them
    """
    if not data.flow_token or not data.pin:
        return {"success": False, "error": "Missing PIN or token"}

    # --- Extract chat_id fallback from flow_token ---
    if not data.chat_id and "-" in data.flow_token:
        data.chat_id = data.flow_token.split("-")[-1]

    # --- Parse flow_token: "{type}-pin-{idempotency_key}-{phone}" ---
    parts = data.flow_token.split("-", 2)
    flow_type = parts[0] if parts else "unknown"

    # Extract idempotency_key (everything between "-pin-" and the last "-{phone}")
    idem_key: str | None = None
    if "-pin-" in data.flow_token:
        after_pin = data.flow_token.split("-pin-", 1)[1]  # "{idem_key}-{phone}"
        # The last segment is the phone/chat_id
        idem_parts = after_pin.rsplit("-", 1)
        idem_key = idem_parts[0] if len(idem_parts) > 1 else after_pin
    token_remainder = data.flow_token.split("-pin-", 1)[-1] if "-pin-" in data.flow_token else data.flow_token

    # --- Look up the real phone_number from Redis (chat_id != phone for Telegram) ---
    from shared.cache.redis_client import RedisClient
    from shared.services.auth.authorization import AuthorizationService

    redis_client = RedisClient.get_client()

    # Resolve real phone number from transaction token stored during flow creation
    phone_number: str | None = None
    if idem_key:
        for prefix in ("transaction", "transfer", "airtime", "data"):
            phone_number = await redis_client.get(f"{prefix}:token:{idem_key}:phone")
            if phone_number:
                break

    if not phone_number:
        # Fallback: the last segment of flow_token might be the phone on WhatsApp,
        # but on Telegram it's the chat_id. Try to look up user by channel identity.
        user_repo = UserRepository(db)
        user = await user_repo.get_by_channel_identity("telegram", data.chat_id)
        if user and user.phone_number:
            phone_number = str(user.phone_number)
        else:
            return {"success": False, "error": "Session expired. Please start a new transaction."}

    safe_phone_number = str(phone_number)

    auth_service = AuthorizationService(redis_client=redis_client)
    auth_result = await auth_service.verify_pin(
        phone_number=safe_phone_number,
        pin=str(data.pin),
        idempotency_key=idem_key or token_remainder,
        transaction_type=flow_type if flow_type != "unknown" else None,
    )

    if not auth_result.transaction_type:
        auth_result.transaction_type = flow_type

    await auth_service.store_pin_verification_result(idem_key or token_remainder, auth_result)

    if not auth_result.verified:
        logger.info("telegram_pin_verification_failed", chat_id=data.chat_id, error=auth_result.error)
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
        idempotency_key=token_remainder,
        success=True,
        channel="telegram",
        extra_data={"pin": data.pin, "source": "telegram_mini_app_rest", "chat_id": data.chat_id},
    )

    try:
        publisher = QueuePublisherFactory.get_publisher()
        await publisher.publish(
            topic="flow_event.process",
            message=cast(dict, event.to_dict()),
        )
        logger.info("telegram_pin_rest_published", chat_id=data.chat_id, flow_type=resolved_flow_type)

        if data.chat_id:
            try:
                stored_msg_id = await redis_client.getdel(f"tg:pin_msg:{data.flow_token}")
                if stored_msg_id:
                    _telegram = TelegramClient()
                    await _telegram.mark_as_authorized(data.chat_id, stored_msg_id)
                    logger.info(
                        "telegram_pin_keyboard_marked_authorized", chat_id=data.chat_id, message_id=stored_msg_id
                    )
            except Exception as kb_err:
                logger.warning("telegram_pin_keyboard_removal_failed", error=str(kb_err))

        return {"success": True}
    except Exception as e:
        logger.error("telegram_pin_rest_failed", error=str(e), exc_info=True)
        return {"success": False, "error": "Internal server error"}
