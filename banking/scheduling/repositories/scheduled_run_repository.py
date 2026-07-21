"""Repository for ScheduledRun model."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.enums import ScheduledRunStatusEnum
from shared.database.models import ScheduledInstruction, ScheduledRun


class ScheduledRunRepository(BaseRepository[ScheduledRun]):
    """Repository for scheduled run operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ScheduledRun)

    async def get_by_idempotency_key(self, idempotency_key: str) -> ScheduledRun | None:
        """Get run by deterministic idempotency key."""
        result = await self.db.execute(select(ScheduledRun).filter(ScheduledRun.idempotency_key == idempotency_key))
        return result.scalars().first()

    @staticmethod
    def _user_lookup(value: str):  # type: ignore[no-untyped-def]
        from uuid import UUID

        try:
            return UUID(value)
        except ValueError:
            return value

    def _user_query(
        self,
        user_id: str,
        *,
        schedule_ids: list[str] | None = None,
        domains: list[str] | None = None,
        statuses: list[str] | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ):  # type: ignore[no-untyped-def]
        query = (
            select(ScheduledRun)
            .join(ScheduledInstruction, ScheduledInstruction.id == ScheduledRun.schedule_id)
            .filter(ScheduledInstruction.user_id == self._user_lookup(user_id))
        )
        if schedule_ids:
            query = query.filter(ScheduledRun.schedule_id.in_(schedule_ids))
        if domains:
            query = query.filter(ScheduledInstruction.domain.in_([value.casefold() for value in domains]))
        if statuses:
            query = query.filter(ScheduledRun.status.in_([value.casefold() for value in statuses]))
        if starts_at:
            query = query.filter(ScheduledRun.due_at_utc >= starts_at)
        if ends_at:
            query = query.filter(ScheduledRun.due_at_utc <= ends_at)
        return query

    async def get_filtered_by_user(
        self,
        user_id: str,
        *,
        schedule_ids: list[str] | None = None,
        domains: list[str] | None = None,
        statuses: list[str] | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        limit: int = 6,
        offset: int = 0,
    ) -> list[ScheduledRun]:
        query = self._user_query(
            user_id,
            schedule_ids=schedule_ids,
            domains=domains,
            statuses=statuses,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        result = await self.db.execute(
            query.order_by(ScheduledRun.due_at_utc.desc(), ScheduledRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_filtered_by_user(
        self,
        user_id: str,
        *,
        schedule_ids: list[str] | None = None,
        domains: list[str] | None = None,
        statuses: list[str] | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ) -> int:
        query = self._user_query(
            user_id,
            schedule_ids=schedule_ids,
            domains=domains,
            statuses=statuses,
            starts_at=starts_at,
            ends_at=ends_at,
        ).with_only_columns(func.count(ScheduledRun.id)).order_by(None)
        result = await self.db.execute(query)
        return int(result.scalar_one())

    async def get_for_user(self, run_id: str, user_id: str) -> ScheduledRun | None:
        query = self._user_query(user_id).filter(ScheduledRun.id == run_id)
        result = await self.db.execute(query)
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
