"""Mono webhook router - thin controller for Mono events."""
from fastapi import APIRouter, Request, Response

from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

from apps.gateway.core.config import settings
from apps.gateway.api.webhooks.mono.service import MonoWebhookService

router = APIRouter(prefix="/webhook", tags=["webhooks"])
logger = get_logger(__name__)

_service_instance = None


def _get_service() -> MonoWebhookService:
    """Get or create Mono webhook service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = MonoWebhookService(
            whatsapp_client=WhatsAppClient(),
            queue=RedisQueue(redis_url=settings.redis_url),
        )
    return _service_instance


@router.post("/mono")
async def mono_webhook(request: Request) -> Response:
    """Handle Mono webhook events for mandate and debit status updates."""
    try:
        payload = await request.json()
        event = payload.get("event", "")
        data = payload.get("data", {})

        logger.info("mono_webhook_received", event=event, data_id=data.get("id"))

        service = _get_service()

        # Handle mandate events
        if event.startswith("events.mandate"):
            await service.handle_mandate_event(event, data)
            return Response(status_code=200)

        # Handle debit events
        if event.startswith("events.mandates.debit"):
            await service.handle_debit_event(event, data)
            return Response(status_code=200)

        logger.debug("mono_webhook_ignored", event=event)
        return Response(status_code=200)

    except Exception as e:
        logger.error("mono_webhook_error", error=str(e), exc_info=True)
        return Response(status_code=200)
