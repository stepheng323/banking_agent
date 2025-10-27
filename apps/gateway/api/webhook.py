from fastapi import APIRouter, Request, Response, HTTPException, status
from apps.gateway.core.config import settings
from apps.gateway.adapters.meta_whatsapp import parse_payload, verify_meta_signature
from apps.gateway.adapters.sender import send_text
from shared.queue.redis_queue import RedisQueue
from shared.models.messages import WhatsAppMessage, MessageType, MessagePriority
from shared.clients.whatsapp_client import WhatsAppClient
from datetime import datetime

router = APIRouter()

queue = RedisQueue(redis_url=settings.redis_url)
whatsapp_client = WhatsAppClient()


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
async def whatsapp_webhook(request: Request) -> Response:
    try:
        await verify_meta_signature(request)
        payload = await request.json()

        print(f"📥 Received webhook payload")

        messages = parse_payload(payload)

        print(f"🔍 Messages: {messages}")

        for msg in messages:
            text = msg.get("text") or ""
            from_id = msg["from"]
            message_id = msg.get("id", "unknown")
            msg_type = msg.get("type", "text")
            flow_data = msg.get("flow_data")

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
                    timestamp=datetime.utcnow(),
                    priority=MessagePriority.NORMAL,
                )
                try:
                    await queue.enqueue_simple(
                        queue_name="banking:messages",
                        message=whatsapp_msg.model_dump(mode="json"),
                    )
                    print(f" ✅ Text message enqueued for processing")

                    try:
                        await whatsapp_client.send_typing_indicator(
                            message_id=message_id
                        )
                    except Exception as typing_error:
                        print(f"   ⚠️  Could not send typing indicator: {typing_error}")

                except Exception as queue_error:
                    print(f"   ❌ Failed to enqueue message: {queue_error}")
                    await send_text(
                        to=from_id,
                        text="Sorry, I'm having trouble processing your message right now.",
                    )

            # Process flow completion messages
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
                    print(f" ✅ Flow response enqueued for processing")
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
