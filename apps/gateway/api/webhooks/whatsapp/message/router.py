"""WhatsApp webhook router - thin controller for WhatsApp events."""

from fastapi import APIRouter, HTTPException, Request, Response, status

from apps.gateway.adapters.meta_whatsapp import verify_meta_signature
from apps.gateway.core.config import settings
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

from .service import WhatsAppWebhookService

router = APIRouter(prefix="/webhook", tags=["webhooks"])
logger = get_logger(__name__)

_queue_instance = None
_service_instance = None


def get_redis_queue() -> RedisQueue:
    """Dependency factory for Redis queue."""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = RedisQueue(redis_url=settings.redis_url)
    return _queue_instance


def _get_service() -> WhatsAppWebhookService:
    """Get or create WhatsApp webhook service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = WhatsAppWebhookService(queue=get_redis_queue())
    return _service_instance


@router.get("/whatsapp")
async def verify_webhook(request: Request) -> Response:
    """Handle Meta webhook verification challenge."""
    params = request.query_params
    mode = params.get("hub.mode")
    verify_token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and verify_token == settings.meta_verify_token:
        return Response(content=challenge or "", media_type="text/plain")

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> Response:
    """Handle incoming WhatsApp messages."""
    try:
        await verify_meta_signature(request)
        payload = await request.json()

        service = _get_service()
        await service.process_payload(payload)

        return Response(status_code=200)

    except Exception as e:
        logger.error("webhook_error", error=str(e), exc_info=True)
        return Response(status_code=200)
