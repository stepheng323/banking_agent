"""Repository for ScheduledRun model."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.enums import ScheduledRunStatusEnum
from shared.database.models import ScheduledRun
from shared.repositories.base import BaseRepository


class ScheduledRunRepository(BaseRepository[ScheduledRun]):
    """Repository for scheduled run operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ScheduledRun)

    async def get_by_idempotency_key(self, idempotency_key: str) -> ScheduledRun | None:
        """Get run by deterministic idempotency key."""
        result = await self.db.execute(select(ScheduledRun).filter(ScheduledRun.idempotency_key == idempotency_key))
        return result.scalars().first()

    async def mark_processing(self, schedule_run_id: str) -> ScheduledRun | None:
        """Mark scheduled run as processing."""
        run = await self.get_by_id(schedule_run_id)
        if not run:
            return None
        run.status = ScheduledRunStatusEnum.PROCESSING.value
        self.db.add(run)
        await self.db.flush()
        return run

    async def mark_successful(self, schedule_run_id: str, *, transaction_id: str | None = None) -> ScheduledRun | None:
        """Mark scheduled run as successful."""
        run = await self.get_by_id(schedule_run_id)
        if not run:
            return None
        run.status = ScheduledRunStatusEnum.SUCCESSFUL.value
        run.transaction_id = transaction_id or run.transaction_id
        run.completed_at = datetime.now(UTC).replace(tzinfo=None)
        self.db.add(run)
        await self.db.flush()
        return run

    async def mark_failed(self, schedule_run_id: str, *, error_message: str | None = None) -> ScheduledRun | None:
        """Mark scheduled run as failed."""
        run = await self.get_by_id(schedule_run_id)
        if not run:
            return None
        run.status = ScheduledRunStatusEnum.FAILED.value
        run.error_message = error_message
        run.completed_at = datetime.now(UTC).replace(tzinfo=None)
        self.db.add(run)
        await self.db.flush()
        return run
