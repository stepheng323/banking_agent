"""Telegram webhook router — FastAPI endpoint for Telegram Bot updates."""

import hmac
from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.gateway.api.webhooks.telegram.auth import verify_telegram_init_data
from apps.gateway.api.webhooks.telegram.onboarding import router as onboarding_router
from apps.gateway.api.webhooks.telegram.service import TelegramWebhookService
from apps.gateway.api.webhooks.telegram.session import (
    invalid_telegram_session_error,
    telegram_init_user_id,
    token_fingerprint,
)
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
from shared.database.connection import get_db
from shared.queue.factory import QueuePublisherFactory
from shared.queue.messages import FlowEvent, FlowEventType
from banking.identity.repositories.user_repository import UserRepository
from banking.identity.channel_linking.authorization import is_channel_link_pin_token
from banking.identity.channel_linking.pin_completion import complete_channel_link_with_pin
from banking.identity.channel_linking.telegram_miniapp_bootstrap import consume_telegram_miniapp_bootstrap
from shared.utils.logging import get_logger

router = APIRouter(prefix="/webhook", tags=["telegram"])
router.include_router(onboarding_router)
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
            provided_token_hash=token_fingerprint(x_telegram_bot_api_secret_token),
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


class PinSubmitInput(BaseModel):
    flow_token: str
    pin: str
    chat_id: str = ""
    message_id: str = ""


class TelegramBootstrapInput(BaseModel):
    boot: str
    endpoint: str


@router.post("/telegram/bootstrap")
async def telegram_bootstrap(
    data: TelegramBootstrapInput,
    user_data: dict = Depends(verify_telegram_init_data),
) -> dict:
    """Exchange a short-lived Mini App bootstrap nonce for the server-side flow token."""
    init_user_id = telegram_init_user_id(user_data)
    if not init_user_id:
        return invalid_telegram_session_error()

    try:
        bootstrap = await consume_telegram_miniapp_bootstrap(
            nonce=data.boot,
            endpoint=data.endpoint,
            init_user_id=init_user_id,
        )
    except ValueError:
        logger.warning("telegram_miniapp_bootstrap_invalid_endpoint", endpoint=data.endpoint)
        return invalid_telegram_session_error()

    if not bootstrap:
        return invalid_telegram_session_error()

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

    init_user_id = telegram_init_user_id(user_data)
    if not init_user_id:
        return {"success": False, "error": "Telegram authentication missing. Reopen this page from Telegram."}
    if not data.chat_id or data.chat_id != init_user_id:
        logger.warning(
            "telegram_pin_submit_chat_mismatch",
            chat_id_hash=token_fingerprint(data.chat_id),
            init_user_id_hash=token_fingerprint(init_user_id),
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
            flow_token_hash=token_fingerprint(data.flow_token),
            token_channel_id_hash=token_fingerprint(token_channel_id),
            init_user_id_hash=token_fingerprint(init_user_id),
        )
        return {"success": False, "error": "This PIN request is not valid for this Telegram account."}

    # --- Look up the real phone_number from Redis (chat_id != phone for Telegram) ---
    from shared.cache.redis_client import RedisClient
    from banking.security.authorization import AuthorizationService

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
            chat_id_hash=token_fingerprint(data.chat_id),
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
            chat_id_hash=token_fingerprint(data.chat_id),
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
                        chat_id_hash=token_fingerprint(data.chat_id),
                        message_id_hash=token_fingerprint(stored_msg_id),
                    )
            except Exception as kb_err:
                logger.warning("telegram_pin_keyboard_removal_failed", error=str(kb_err))

        return {"success": True}
    except Exception as e:
        logger.error("telegram_pin_rest_failed", error=str(e), exc_info=True)
        return {"success": False, "error": "Internal server error"}
