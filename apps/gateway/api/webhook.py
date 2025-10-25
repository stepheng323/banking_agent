from fastapi import APIRouter, Request, Response, HTTPException, status
from apps.gateway.core.config import settings
from apps.gateway.adapters.meta_whatsapp import parse_payload, verify_meta_signature
from apps.gateway.adapters.sender import send_text

router = APIRouter()


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
        print(f"📨 Parsed {len(messages)} message(s)")

        for msg in messages:
            text = msg.get("text") or ""
            from_id = msg["from"]
            print(f"   Message from {from_id}: {text}")

            if text:
                try:
                    # Echo the message back (comment out if token is invalid)
                    await send_text(to=from_id, text=f"Echo: {text}")
                except Exception as send_error:
                    # Log the error but don't fail the webhook
                    print(
                        f"⚠️  Failed to send reply, but webhook processed: {send_error}"
                    )

        return Response(status_code=200)

    except Exception as e:
        import traceback
        print(f"❌ Error processing webhook: {e}")
        print(traceback.format_exc())
        return Response(status_code=200)
