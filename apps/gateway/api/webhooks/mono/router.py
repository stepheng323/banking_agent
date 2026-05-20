"""Mono webhook router - thin controller for Mono events."""

import hmac

from fastapi import APIRouter, Request, Response

from apps.gateway.api.webhooks.mono.service import MonoWebhookService
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.queue.factory import QueuePublisherFactory
from shared.utils.logging import get_logger, log_fingerprint

router = APIRouter(prefix="/webhook", tags=["webhooks"])
logger = get_logger(__name__)

_service_instance = None
MONO_WEBHOOK_SECRET_HEADER = "mono-webhook-secret"


def _get_service() -> MonoWebhookService:
    """Get or create Mono webhook service instance."""
    global _service_instance
    if _service_instance is None:
        publisher = QueuePublisherFactory.get_async_publisher()
        _service_instance = MonoWebhookService(publisher=publisher, redis_client=RedisClient.get_client())
    return _service_instance


def _is_authorized_mono_webhook(request: Request) -> bool:
    """Validate Mono webhook secret before parsing provider payloads."""
    configured_secret = settings.mono_webhook_secret
    provided_secret = request.headers.get(MONO_WEBHOOK_SECRET_HEADER, "")

    if not configured_secret:
        if settings.runtime.is_local:
            return True
        logger.error("mono_webhook_secret_not_configured")
        return False

    if not provided_secret:
        logger.warning("mono_webhook_missing_secret_header")
        return False

    if not hmac.compare_digest(provided_secret, configured_secret):
        logger.warning("mono_webhook_invalid_secret", secret_hash=log_fingerprint(provided_secret))
        return False

    return True


@router.post("/mono")
async def mono_webhook(request: Request) -> Response:
    """Handle Mono webhook events for mandate and debit status updates."""
    try:
        if not _is_authorized_mono_webhook(request):
            return Response(status_code=401)

        payload = await request.json()
        event = payload.get("event", "")
        data = payload.get("data", {})

        logger.info("mono_webhook_received", event_name=event, data_id_hash=log_fingerprint(data.get("id")))

        service = _get_service()

        if event in service.DEBIT_STATUS_MAP:
            await service.handle_debit_event(event, data)
            return Response(status_code=200)

        if event in service.MANDATE_STATUS_MAP:
            await service.handle_mandate_event(event, data)
            return Response(status_code=200)

        logger.debug("mono_webhook_ignored", event_name=event)
        return Response(status_code=200)

    except Exception as e:
        logger.error("mono_webhook_error", error=str(e), exc_info=True)
        return Response(status_code=200)
