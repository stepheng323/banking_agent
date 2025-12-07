from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from shared.clients.whatsapp_client import WhatsAppClient
from shared.models.messages import MessagePriority, MessageType, WhatsAppMessage
from shared.queue.redis_queue import RedisQueue

from apps.gateway.adapters.meta_whatsapp import parse_payload, verify_meta_signature
from apps.gateway.adapters.sender import send_text
from apps.gateway.core.config import settings

router = APIRouter()

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

        print("📥 Received webhook payload")

        messages = parse_payload(payload)

        print(f"🔍 Messages: {messages}")

        for msg in messages:
            text = msg.get("text") or ""
            from_id = msg["from"]
            message_id = msg.get("id", "unknown")
            msg_type = msg.get("type", "text")
            flow_data = msg.get("flow_data")

            # Temporary whitelist check
            ALLOWED_NUMBER = "2348162511023"
            if from_id != ALLOWED_NUMBER:
                print(f"⛔ Ignoring message from unauthorized user: {from_id}")
                continue

            print(f"   Message from {from_id}: {msg_type}")
            if text:
                print(f"   Text: {text}")
            if flow_data:
                print(f"   Flow data: {flow_data}")

            if text:
                whatsapp_msg = WhatsAppMessage(
                    message_id=message_id,
                    from_number=from_id,
                    message_type=MessageType.TEXT,
                    text=text,
                    flow_data=flow_data,
                    timestamp=datetime.utcnow(),
                    priority=MessagePriority.NORMAL,
                )
                try:
                    await queue.enqueue_simple(
                        queue_name="banking:messages",
                        message=whatsapp_msg.model_dump(mode="json"),
                    )
                    print(" ✅ Text message enqueued for processing")

                    try:
                        await whatsapp_client.send_typing_indicator(message_id=message_id)
                    except Exception as typing_error:
                        print(f"   ⚠️  Could not send typing indicator: {typing_error}")

                except Exception as queue_error:
                    print(f"   ❌ Failed to enqueue message: {queue_error}")
                    await send_text(
                        to=from_id,
                        text="Sorry, I'm having trouble processing your message right now.",
                    )

            elif msg_type == "interactive" and flow_data:
                whatsapp_msg = WhatsAppMessage(
                    message_id=message_id,
                    from_number=from_id,
                    message_type=MessageType.FLOW,
                    text=None,
                    flow_data=flow_data,
                    timestamp=datetime.utcnow(),
                    priority=MessagePriority.HIGH,
                )
                try:
                    await queue.enqueue_simple(
                        queue_name="banking:messages",
                        message=whatsapp_msg.model_dump(mode="json"),
                    )
                    print(" ✅ Flow response enqueued for processing")
                except Exception as queue_error:
                    print(f"   ❌ Failed to enqueue flow message: {queue_error}")
                    await send_text(
                        to=from_id,
                        text="Sorry, I'm having trouble processing your submission right now.",
                    )

        return Response(status_code=200)

    except Exception as e:
        import traceback

        print(f"❌ Error processing webhook: {e}")
        print(traceback.format_exc())
        return Response(status_code=200)
