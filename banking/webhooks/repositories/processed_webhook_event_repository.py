"""Repository for provider webhook event deduplication."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import ProcessedWebhookEvent
from banking.persistence.base import BaseRepository


class ProcessedWebhookEventRepository(BaseRepository[ProcessedWebhookEvent]):
    """Stores provider webhook event IDs so retries do not re-run side effects."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ProcessedWebhookEvent)

    async def get_by_provider_event_id(
        self,
        provider: str,
        event_id: str,
        *,
        for_update: bool = False,
    ) -> ProcessedWebhookEvent | None:
        """Get an event ledger row by provider and provider event ID."""
        query = select(ProcessedWebhookEvent).filter(
            ProcessedWebhookEvent.provider == provider,
            ProcessedWebhookEvent.event_id == event_id,
        )
        if for_update:
            query = query.with_for_update()

        result = await self.db.execute(query)
        return result.scalars().first()

    async def claim(
        self,
        *,
        provider: str,
        event_id: str,
        event_name: str,
        payload_hash: str | None = None,
    ) -> bool:
        """Claim a webhook event for processing.

        Returns False when the event is already processing or processed. Failed
        events may be claimed again to support manual/provider replay.
        """
        existing = await self.get_by_provider_event_id(provider, event_id, for_update=True)
        now = datetime.now(UTC).replace(tzinfo=None)
        if existing:
            if existing.status != "failed":
                return False
            existing.status = "processing"
            existing.event_name = event_name
            existing.payload_hash = payload_hash or existing.payload_hash
            existing.attempt_count = int(existing.attempt_count or 0) + 1
            existing.last_seen_at = now
            existing.error_message = None
            self.db.add(existing)
            await self.db.flush()
            return True

        event = ProcessedWebhookEvent(
            provider=provider,
            event_id=event_id,
            event_name=event_name,
            status="processing",
            payload_hash=payload_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
        self.db.add(event)
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            return False
        return True

    async def mark_processed(self, *, provider: str, event_id: str) -> ProcessedWebhookEvent | None:
        """Mark a claimed webhook event as processed."""
        event = await self.get_by_provider_event_id(provider, event_id, for_update=True)
        if not event:
            return None
        now = datetime.now(UTC).replace(tzinfo=None)
        event.status = "processed"
        event.processed_at = now
        event.last_seen_at = now
        event.error_message = None
        self.db.add(event)
        await self.db.flush()
        return event

    async def mark_failed(
        self,
        *,
        provider: str,
        event_id: str,
        error_message: str | None = None,
    ) -> ProcessedWebhookEvent | None:
        """Mark a claimed webhook event as failed so it can be replayed."""
        event = await self.get_by_provider_event_id(provider, event_id, for_update=True)
        if not event:
            return None
        if event.status == "processed":
            return event
        event.status = "failed"
        event.last_seen_at = datetime.now(UTC).replace(tzinfo=None)
        event.error_message = error_message
        self.db.add(event)
        await self.db.flush()
        return event
