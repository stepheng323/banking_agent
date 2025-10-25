from fastapi import APIRouter, Request, Response, HTTPException
from bot.core.config import settings
from bot.adapters.meta_whatsapp import parse_payload, verify_meta_signature
from bot.adapters.sender import send_text
from starlette import status

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
    await verify_meta_signature(request)
    payload = await request.json()
    messages = parse_payload(payload)
    for msg in messages:
        text = msg.get("text") or ""
        from_id = msg["from"]
        if text:
            await send_text(to=from_id, text=text)
    return Response(status_code=200)
