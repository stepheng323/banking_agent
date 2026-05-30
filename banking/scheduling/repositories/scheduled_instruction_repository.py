"""Repository for ScheduledInstruction model."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.database.models import ScheduledInstruction


class ScheduledInstructionRepository(BaseRepository[ScheduledInstruction]):
    """Repository for scheduled instruction operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ScheduledInstruction)

    @staticmethod
    def _uuid_or_str(value: str) -> UUID | str:
        try:
            return UUID(value)
        except ValueError:
            return value

    async def get_active_by_user(self, user_id: str, limit: int = 20) -> list[ScheduledInstruction]:
        """Get active schedules for a user."""
        lookup_id = self._uuid_or_str(user_id)
        result = await self.db.execute(
            select(ScheduledInstruction)
            .filter(
                ScheduledInstruction.user_id == lookup_id,
                ScheduledInstruction.status == ScheduledInstructionStatusEnum.ACTIVE.value,
            )
            .order_by(ScheduledInstruction.next_run_at_utc.asc(), ScheduledInstruction.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_due_active(self, *, now_utc: datetime, limit: int = 50) -> list[ScheduledInstruction]:
        """Get due active schedules with DB row locking to avoid duplicate dispatch."""
        naive_now = now_utc.astimezone(UTC).replace(tzinfo=None)
        result = await self.db.execute(
            select(ScheduledInstruction)
            .filter(
                ScheduledInstruction.status == ScheduledInstructionStatusEnum.ACTIVE.value,
                ScheduledInstruction.next_run_at_utc <= naive_now,
            )
            .order_by(ScheduledInstruction.next_run_at_utc.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def cancel(self, schedule_id: str, user_id: str) -> ScheduledInstruction | None:
        """Cancel an active schedule for a user."""
        lookup_schedule_id = self._uuid_or_str(schedule_id)
        lookup_user_id = self._uuid_or_str(user_id)
        result = await self.db.execute(
            select(ScheduledInstruction).filter(
                ScheduledInstruction.id == lookup_schedule_id,
                ScheduledInstruction.user_id == lookup_user_id,
                ScheduledInstruction.status == ScheduledInstructionStatusEnum.ACTIVE.value,
            )
        )
        schedule = result.scalars().first()
        if not schedule:
            return None

        schedule.status = ScheduledInstructionStatusEnum.CANCELLED.value
        schedule.cancelled_at = datetime.now(UTC).replace(tzinfo=None)
        self.db.add(schedule)
        await self.db.flush()
        return schedule

    async def get_active_for_user_for_update(self, schedule_id: str, user_id: str) -> ScheduledInstruction | None:
        """Get an active schedule for update, locking the row for the transaction."""
        lookup_schedule_id = self._uuid_or_str(schedule_id)
        lookup_user_id = self._uuid_or_str(user_id)
        result = await self.db.execute(
            select(ScheduledInstruction)
            .filter(
                ScheduledInstruction.id == lookup_schedule_id,
                ScheduledInstruction.user_id == lookup_user_id,
                ScheduledInstruction.status == ScheduledInstructionStatusEnum.ACTIVE.value,
            )
            .with_for_update(nowait=True)
        )
        return result.scalars().first()
