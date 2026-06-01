"""Mono webhook router - thin controller for Mono events."""

import hashlib
import hmac
import json
from typing import cast

from fastapi import APIRouter, Request, Response

from apps.gateway.api.webhooks.mono.service import MonoWebhookService
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.messaging.delivery.service import DeliveryService
from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.async_group_types import AsyncGroupRedis
from banking.transactions.runtime.bill_completion_notifications import BillCompletionNotifier
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
        redis_client = RedisClient.get_client()
        async_group_redis = cast(AsyncGroupRedis, redis_client)
        delivery_service = DeliveryService()
        _service_instance = MonoWebhookService(
            publisher=publisher,
            delivery_service=delivery_service,
            redis_client=redis_client,
            bill_completion_notifier=BillCompletionNotifier(
                delivery_service=delivery_service,
                redis_client=async_group_redis,
                beneficiary_suggestion_service=BeneficiarySuggestionService(publisher),
            ),
        )
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


def _payload_hash(payload: dict) -> str:
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


async def _claim_mono_webhook_event(*, event_id: str, event_name: str, payload: dict) -> bool:
    """Claim a Mono webhook event ID before running business side effects."""
    if not event_id:
        return True

    async with UnitOfWork() as uow:
        if not uow.processed_webhook_events:
            return True
        return await uow.processed_webhook_events.claim(
            provider="mono",
            event_id=event_id,
            event_name=event_name,
            payload_hash=_payload_hash(payload),
        )
    return True


async def _mark_mono_webhook_event_processed(*, event_id: str) -> None:
    if not event_id:
        return
    async with UnitOfWork() as uow:
        if uow.processed_webhook_events:
            await uow.processed_webhook_events.mark_processed(provider="mono", event_id=event_id)


async def _mark_mono_webhook_event_failed(*, event_id: str, error: str) -> None:
    if not event_id:
        return
    async with UnitOfWork() as uow:
        if uow.processed_webhook_events:
            await uow.processed_webhook_events.mark_failed(provider="mono", event_id=event_id, error_message=error)


@router.post("/mono")
async def mono_webhook(request: Request) -> Response:
    """Handle Mono webhook events for mandate and debit status updates."""
    event_id = ""
    try:
        if not _is_authorized_mono_webhook(request):
            return Response(status_code=401)

        payload = await request.json()
        event = str(payload.get("event") or "")
        event_id = str(payload.get("event_id") or "")
        data = payload.get("data", {})

        logger.info("mono_webhook_received", event_name=event, data_id_hash=log_fingerprint(data.get("id")))

        if event_id:
            claimed = await _claim_mono_webhook_event(event_id=event_id, event_name=event, payload=payload)
            if not claimed:
                logger.info(
                    "mono_webhook_duplicate_ignored",
                    event_name=event,
                    event_id_hash=log_fingerprint(event_id),
                )
                return Response(status_code=200)

        service = _get_service()

        if event in service.DEBIT_STATUS_MAP:
            handled = await service.handle_debit_event(event, data)
            if handled:
                await _mark_mono_webhook_event_processed(event_id=event_id)
            else:
                await _mark_mono_webhook_event_failed(event_id=event_id, error="mono_debit_event_not_processed")
            return Response(status_code=200)

        if event in service.MANDATE_STATUS_MAP:
            handled = await service.handle_mandate_event(event, data)
            if handled:
                await _mark_mono_webhook_event_processed(event_id=event_id)
            else:
                await _mark_mono_webhook_event_failed(event_id=event_id, error="mono_mandate_event_not_processed")
            return Response(status_code=200)

        logger.debug("mono_webhook_ignored", event_name=event)
        await _mark_mono_webhook_event_processed(event_id=event_id)
        return Response(status_code=200)

    except Exception as e:
        try:
            await _mark_mono_webhook_event_failed(event_id=event_id, error=str(e))
        except Exception as mark_error:
            logger.error("mono_webhook_event_mark_failed_error", error=str(mark_error), exc_info=True)
        logger.error("mono_webhook_error", error=str(e), exc_info=True)
        return Response(status_code=200)
