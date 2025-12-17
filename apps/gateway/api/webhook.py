from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from shared.clients.whatsapp_client import WhatsAppClient
from shared.models.messages import MessagePriority, MessageType, WhatsAppMessage
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

from apps.gateway.adapters.meta_whatsapp import parse_payload, verify_meta_signature
from apps.gateway.adapters.sender import send_text
from apps.gateway.core.config import settings

router = APIRouter()
logger = get_logger(__name__)

_redis_queue_instance = None
_whatsapp_client_instance = None


def get_redis_queue() -> RedisQueue:
    """Dependency factory for Redis queue with lazy initialization."""
    global _redis_queue_instance
    if _redis_queue_instance is None:
        _redis_queue_instance = RedisQueue(redis_url=settings.redis_url)
    return _redis_queue_instance


def get_whatsapp_client() -> WhatsAppClient:
    """Dependency factory for WhatsApp client with lazy initialization."""
    global _whatsapp_client_instance
    if _whatsapp_client_instance is None:
        _whatsapp_client_instance = WhatsAppClient()
    return _whatsapp_client_instance


@router.get("/webhook/whatsapp")
async def verify_webhook(request: Request) -> Response:
    params = request.query_params
    mode = params.get("hub.mode")
    verify_token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and verify_token == settings.meta_verify_token:
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    queue: RedisQueue = Depends(get_redis_queue),
    whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client),
) -> Response:
    try:
        await verify_meta_signature(request)
        payload = await request.json()

        messages = parse_payload(payload)

        for msg in messages:
            text = msg.get("text") or ""
            from_id = msg["from"]
            message_id = msg.get("id", "unknown")
            msg_type = msg.get("type", "text")
            flow_data = msg.get("flow_data")

            # Temporary whitelist check
            ALLOWED_NUMBER = "2348162511023"
            if from_id != ALLOWED_NUMBER:
                logger.debug("webhook_message_filtered", from_id=from_id)
                continue

            logger.info("webhook_message_received", from_id=from_id, msg_type=msg_type)
            
            priority = MessagePriority.NORMAL
            if msg_type == "interactive":
                priority = MessagePriority.HIGH
            
            # Handle regular messages (text, image, audio)
            # For interactive messages, only handle if there's NO flow_data
            # (flow_data is handled by the /webhook/flow endpoint)
            is_regular_message = msg_type in ("text", "image", "audio")
            is_interactive_without_flow = msg_type == "interactive" and not flow_data
            
            if is_regular_message or is_interactive_without_flow:
                media_id = msg.get("media_id")
                mime_type = msg.get("mime_type")
                
                try:
                    enum_type = MessageType(msg_type)
                except ValueError:
                    enum_type = MessageType.TEXT

                whatsapp_msg = WhatsAppMessage(
                    message_id=message_id,
                    from_number=from_id,
                    message_type=enum_type,
                    text=text,
                    flow_data=flow_data,
                    media_id=media_id,
                    mime_type=mime_type,
                    timestamp=datetime.utcnow(),
                    priority=priority,
                )
                
                try:
                    await queue.enqueue_simple(
                        queue_name="banking:messages",
                        message=whatsapp_msg.model_dump(mode="json"),
                    )
                    logger.info("message_enqueued", msg_type=msg_type, from_id=from_id)

                    try:
                         if msg_type != "interactive":
                            await whatsapp_client.send_typing_indicator(message_id=message_id)
                    except Exception:
                        pass  # Typing indicator is not critical

                except Exception as queue_error:
                    logger.error("message_enqueue_failed", error=str(queue_error))
                    await send_text(
                        to=from_id,
                        text="Sorry, I'm having trouble processing your message right now.",
                    )

            elif msg_type == "interactive" and flow_data:
                # Flow responses are handled by the /webhook/flow endpoint
                logger.debug("flow_response_skipped", from_id=from_id)

        return Response(status_code=200)

    except Exception as e:
        logger.error("webhook_error", error=str(e), exc_info=True)
        return Response(status_code=200)


@router.post("/webhook/mono")
async def mono_webhook(request: Request) -> Response:
    """Handle Mono webhook events for mandate status updates."""
    try:
        payload = await request.json()
        event = payload.get("event", "")
        data = payload.get("data", {})
        
        logger.info("mono_webhook_received", event=event, mandate_id=data.get("id"))
        
        event_status_map = {
            "events.mandates.approved": "approved",
            "events.mandates.ready": "ready",
            "events.mandates.rejected": "rejected",
            "events.mandate.action.cancel": "cancelled",
            "events.mandate.action.pause": "paused",
            "events.mandate.action.reinstate": "ready",
        }
        
        if event not in event_status_map:
            logger.debug("mono_webhook_ignored", event=event)
            return Response(status_code=200)
        
        mandate_id = data.get("id")
        if not mandate_id:
            logger.warning("mono_webhook_no_mandate_id", event=event)
            return Response(status_code=200)
        
        new_status = event_status_map[event]
        
        from shared.repositories.unit_of_work import UnitOfWork
        
        with UnitOfWork() as uow:
            if uow.accounts:
                account = uow.accounts.update_mandate_status(mandate_id, new_status)
                if account:
                    logger.info(
                        "mandate_status_updated",
                        mandate_id=mandate_id,
                        status=new_status,
                        account_id=str(account.id),
                    )
                    
                    # Invalidate account cache so user gets fresh data
                    user = uow.users.get_by_id(str(account.user_id)) if uow.users else None
                    if user and user.phone_number:
                        try:
                            from shared.cache.user_data import UserDataCache
                            cache = UserDataCache()
                            await cache.invalidate_accounts(user.phone_number)
                            logger.debug("account_cache_invalidated", phone=user.phone_number)
                        except Exception as e:
                            logger.warning("cache_invalidation_failed", error=str(e))
                        
                        # Notify user when mandate is ready
                        if new_status == "ready":
                            try:
                                whatsapp = get_whatsapp_client()
                                await whatsapp.send_text(
                                    to=user.phone_number,
                                    text=f"✅ Great news! Your {account.bank_name} account ({account.account_number}) is now fully set up and ready to use for payments.",
                                )
                            except Exception as e:
                                logger.error("mandate_ready_notification_failed", error=str(e))
                else:
                    logger.warning("mandate_not_found", mandate_id=mandate_id)
        
        return Response(status_code=200)
        
    except Exception as e:
        logger.error("mono_webhook_error", error=str(e), exc_info=True)
        return Response(status_code=200)

