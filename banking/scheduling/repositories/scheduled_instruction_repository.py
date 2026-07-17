"""Repository for ScheduledInstruction model."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
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

    async def get_active_by_user(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ScheduledInstruction]:
        """Get active schedules for a user."""
        lookup_id = self._uuid_or_str(user_id)
        result = await self.db.execute(
            select(ScheduledInstruction)
            .filter(
                ScheduledInstruction.user_id == lookup_id,
                ScheduledInstruction.status == ScheduledInstructionStatusEnum.ACTIVE.value,
            )
            .order_by(ScheduledInstruction.next_run_at_utc.asc(), ScheduledInstruction.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_active_by_user(self, user_id: str) -> int:
        """Count active schedules without truncating a read response."""
        lookup_id = self._uuid_or_str(user_id)
        result = await self.db.execute(
            select(func.count(ScheduledInstruction.id)).filter(
                ScheduledInstruction.user_id == lookup_id,
                ScheduledInstruction.status == ScheduledInstructionStatusEnum.ACTIVE.value,
            )
        )
        return int(result.scalar_one())

    def _filtered_user_query(
        self,
        user_id: str,
        *,
        statuses: list[str] | None = None,
        domains: list[str] | None = None,
        recurrence: str | None = None,
        recipient_name: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        selected_ids: list[str] | None = None,
    ):  # type: ignore[no-untyped-def]
        lookup_id = self._uuid_or_str(user_id)
        query = select(ScheduledInstruction).filter(ScheduledInstruction.user_id == lookup_id)
        normalized_statuses = [
            "active" if status.casefold() == "pending" else status.casefold()
            for status in statuses or []
        ]
        if normalized_statuses:
            query = query.filter(ScheduledInstruction.status.in_(normalized_statuses))
        if domains:
            query = query.filter(ScheduledInstruction.domain.in_([domain.casefold() for domain in domains]))
        if recurrence:
            query = query.filter(ScheduledInstruction.recurrence_type == recurrence.casefold())
        if starts_at:
            query = query.filter(ScheduledInstruction.next_run_at_utc >= starts_at)
        if ends_at:
            query = query.filter(ScheduledInstruction.next_run_at_utc <= ends_at)
        if recipient_name:
            pattern = f"%{recipient_name}%"
            snapshot = ScheduledInstruction.payload_snapshot
            query = query.filter(
                or_(
                    snapshot["recipient_name"].as_string().ilike(pattern),
                    snapshot["recipient_resolved_name"].as_string().ilike(pattern),
                    snapshot["recipient_phone"].as_string().ilike(pattern),
                    snapshot["target_phone"].as_string().ilike(pattern),
                )
            )
        if selected_ids:
            query = query.filter(
                ScheduledInstruction.id.in_([self._uuid_or_str(value) for value in selected_ids])
            )
        return query

    async def get_filtered_by_user(
        self,
        user_id: str,
        *,
        statuses: list[str] | None = None,
        domains: list[str] | None = None,
        recurrence: str | None = None,
        recipient_name: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        selected_ids: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ScheduledInstruction]:
        """Return one stable page using the complete typed filter set."""
        query = self._filtered_user_query(
            user_id,
            statuses=statuses,
            domains=domains,
            recurrence=recurrence,
            recipient_name=recipient_name,
            starts_at=starts_at,
            ends_at=ends_at,
            selected_ids=selected_ids,
        )
        result = await self.db.execute(
            query.order_by(ScheduledInstruction.next_run_at_utc.asc(), ScheduledInstruction.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_filtered_by_user(
        self,
        user_id: str,
        *,
        statuses: list[str] | None = None,
        domains: list[str] | None = None,
        recurrence: str | None = None,
        recipient_name: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        selected_ids: list[str] | None = None,
    ) -> int:
        """Count the same typed repository query used for list pages."""
        query = self._filtered_user_query(
            user_id,
            statuses=statuses,
            domains=domains,
            recurrence=recurrence,
            recipient_name=recipient_name,
            starts_at=starts_at,
            ends_at=ends_at,
            selected_ids=selected_ids,
        ).with_only_columns(func.count(ScheduledInstruction.id)).order_by(None)
        result = await self.db.execute(query)
        return int(result.scalar_one())

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

    async def get_active_ids_for_user_for_update(
        self,
        schedule_ids: list[str],
        user_id: str,
    ) -> list[ScheduledInstruction]:
        """Lock a bounded active schedule selection in one transaction."""
        if not schedule_ids:
            return []
        lookup_ids = [self._uuid_or_str(value) for value in schedule_ids]
        lookup_user_id = self._uuid_or_str(user_id)
        result = await self.db.execute(
            select(ScheduledInstruction)
            .filter(
                ScheduledInstruction.id.in_(lookup_ids),
                ScheduledInstruction.user_id == lookup_user_id,
                ScheduledInstruction.status == ScheduledInstructionStatusEnum.ACTIVE.value,
            )
            .with_for_update(nowait=True)
        )
        return list(result.scalars().all())
