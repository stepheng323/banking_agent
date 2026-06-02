"""Flutterwave webhook service."""

from typing import Any

from shared.observability.events import emit_operational_event
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

LEGACY_TRANSFER_EVENT = "transfer.completed"
TRANSFER_DISBURSE_EVENT = "transfer.disburse"
TRANSFER_EVENT_TYPE = "transfer"


class FlutterwaveWebhookService:
    """Routes Flutterwave payout webhooks into verified reconciliation jobs."""

    def __init__(self, publisher: QueuePublisher | None = None) -> None:
        if publisher is None:
            raise ValueError("publisher is required")
        self.publisher = publisher

    async def handle_event(self, payload: dict[str, Any]) -> bool:
        """Publish a payout reconciliation job for Flutterwave transfer webhooks."""
        event_name = flutterwave_event_name(payload)
        data = flutterwave_transfer_data(payload)
        if not is_flutterwave_transfer_event(payload):
            emit_operational_event(
                "flutterwave_webhook_ignored_non_transfer",
                severity="info",
                domain="webhook",
                details={"event_name": event_name},
            )
            logger.debug("flutterwave_webhook_ignored", event_name=event_name)
            return True

        reference = transfer_reference(data)
        transfer_id = transfer_id_from_data(data)
        status = transfer_status(data)
        if not reference and not transfer_id:
            emit_operational_event(
                "flutterwave_transfer_webhook_missing_reference",
                severity="warning",
                domain="webhook",
                details={"event_name": event_name},
            )
            logger.warning("flutterwave_transfer_webhook_missing_reference", event_name=event_name)
            return False

        await self.publisher.publish(
            topic="payout.reconcile",
            message={
                "source": "flutterwave_webhook",
                "event_id": flutterwave_event_id(payload),
                "event_name": event_name,
                "reference": reference,
                "provider_transfer_id": transfer_id,
                "status_hint": status,
            },
        )
        logger.info(
            "flutterwave_payout_reconciliation_queued",
            event_name=event_name,
            transfer_id_hash=log_fingerprint(transfer_id),
            reference_hash=log_fingerprint(reference),
            status=status,
        )
        return True


def flutterwave_event_name(payload: dict[str, Any]) -> str:
    """Return Flutterwave event name across v3/v4 payload shapes."""
    for key in ("event", "type", "event.type"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def flutterwave_transfer_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Return transfer object from supported Flutterwave webhook payloads."""
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    transfer = payload.get("transfer")
    if isinstance(transfer, dict):
        return transfer
    return {}


def is_flutterwave_transfer_event(payload: dict[str, Any]) -> bool:
    """Return whether the webhook payload describes a transfer/payout event."""
    event = str(payload.get("event") or "").strip().lower()
    event_type = str(payload.get("event.type") or "").strip().lower()
    current_type = str(payload.get("type") or "").strip().lower()

    if current_type == TRANSFER_DISBURSE_EVENT:
        return True

    if event == LEGACY_TRANSFER_EVENT and event_type == TRANSFER_EVENT_TYPE:
        return True

    return event == LEGACY_TRANSFER_EVENT and "event.type" not in payload


def transfer_id_from_data(data: dict[str, Any]) -> str | None:
    """Extract Flutterwave transfer ID from webhook data."""
    for key in ("id", "transfer_id", "transaction_id"):
        value = data.get(key)
        if value is not None:
            return str(value)
    return None


def transfer_reference(data: dict[str, Any]) -> str | None:
    """Extract merchant transfer reference from webhook data."""
    for key in ("reference", "tx_ref"):
        value = data.get(key)
        if value:
            return str(value)
    return None


def transfer_status(data: dict[str, Any]) -> str | None:
    """Extract provider transfer status from webhook data."""
    value = data.get("status")
    return None if value is None else str(value)


def flutterwave_event_id(payload: dict[str, Any]) -> str:
    """Build a stable event ID for Flutterwave retry dedupe."""
    for key in ("webhook_id", "event_id"):
        value = payload.get(key)
        if value:
            return str(value)

    data = flutterwave_transfer_data(payload)
    top_level_id = payload.get("id")
    data_id = transfer_id_from_data(data)
    if top_level_id and str(top_level_id) != str(data_id or ""):
        return str(top_level_id)

    parts = [
        flutterwave_event_name(payload) or "flutterwave",
        data_id or "",
        transfer_reference(data) or "",
        transfer_status(data) or "",
    ]
    event_id = ":".join(part for part in parts if part)
    return event_id or "flutterwave:unknown"
