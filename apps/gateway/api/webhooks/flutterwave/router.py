"""Flutterwave webhook router."""

import json

from fastapi import APIRouter, Request, Response

from apps.gateway.api.webhooks.flutterwave import auth, dependencies, event_ledger
from apps.gateway.api.webhooks.flutterwave.service import (
    flutterwave_event_id,
    flutterwave_event_name,
    flutterwave_transfer_data,
    transfer_id_from_data,
)
from shared.utils.logging import get_logger, log_fingerprint

router = APIRouter(prefix="/webhook", tags=["webhooks"])
logger = get_logger(__name__)


@router.post("/flutterwave")
async def flutterwave_webhook(request: Request) -> Response:
    """Handle Flutterwave webhook events for payout reconciliation."""
    event_id = ""
    action_completed = False
    try:
        raw_body = await request.body()
        if not auth.is_authorized_flutterwave_webhook(request, raw_body):
            return Response(status_code=401)

        payload = json.loads(raw_body.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            logger.warning("flutterwave_webhook_non_object_payload")
            return Response(status_code=400)

        event_name = flutterwave_event_name(payload)
        event_id = flutterwave_event_id(payload)
        data = flutterwave_transfer_data(payload)
        logger.info(
            "flutterwave_webhook_received",
            event_name=event_name,
            event_id_hash=log_fingerprint(event_id),
            transfer_id_hash=log_fingerprint(transfer_id_from_data(data)),
        )

        claimed = await event_ledger.claim_flutterwave_webhook_event(
            event_id=event_id,
            event_name=event_name,
            payload=payload,
        )
        if not claimed:
            logger.info(
                "flutterwave_webhook_duplicate_ignored",
                event_name=event_name,
                event_id_hash=log_fingerprint(event_id),
            )
            return Response(status_code=200)

        service = dependencies.get_flutterwave_webhook_service()
        handled = await service.handle_event(payload)
        if handled:
            action_completed = True
            await event_ledger.mark_flutterwave_webhook_event_processed(event_id=event_id)
        else:
            await event_ledger.mark_flutterwave_webhook_event_failed(
                event_id=event_id,
                error="flutterwave_transfer_event_not_queued",
            )
        return Response(status_code=200)

    except json.JSONDecodeError:
        logger.warning("flutterwave_webhook_invalid_json")
        return Response(status_code=400)
    except Exception as e:
        if event_id and not action_completed:
            try:
                await event_ledger.mark_flutterwave_webhook_event_failed(event_id=event_id, error=str(e))
            except Exception as mark_error:
                logger.error("flutterwave_webhook_event_mark_failed_error", error=str(mark_error), exc_info=True)
        elif event_id:
            logger.error(
                "flutterwave_webhook_post_action_error",
                event_id_hash=log_fingerprint(event_id),
                error=str(e),
                exc_info=True,
            )
        logger.error("flutterwave_webhook_error", error=str(e), exc_info=True)
        return Response(status_code=500)
