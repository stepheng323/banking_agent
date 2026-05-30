"""Flutterwave webhook event ledger helpers."""

import hashlib
import json

from banking.persistence.unit_of_work import UnitOfWork


def payload_hash(payload: dict) -> str:
    """Hash a webhook payload for duplicate event ledger metadata."""
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


async def claim_flutterwave_webhook_event(*, event_id: str, event_name: str, payload: dict) -> bool:
    """Claim a Flutterwave webhook event ID before side effects."""
    async with UnitOfWork() as uow:
        if not uow.processed_webhook_events:
            raise RuntimeError("flutterwave_webhook_event_ledger_unavailable")
        return await uow.processed_webhook_events.claim(
            provider="flutterwave",
            event_id=event_id,
            event_name=event_name,
            payload_hash=payload_hash(payload),
        )


async def mark_flutterwave_webhook_event_processed(*, event_id: str) -> None:
    """Mark a Flutterwave webhook event as processed."""
    async with UnitOfWork() as uow:
        if uow.processed_webhook_events:
            await uow.processed_webhook_events.mark_processed(provider="flutterwave", event_id=event_id)


async def mark_flutterwave_webhook_event_failed(*, event_id: str, error: str) -> None:
    """Mark a Flutterwave webhook event as failed for provider/manual replay."""
    async with UnitOfWork() as uow:
        if uow.processed_webhook_events:
            await uow.processed_webhook_events.mark_failed(
                provider="flutterwave",
                event_id=event_id,
                error_message=error,
            )
