"""Repository for mirrored bank transaction coverage windows."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.models import BankTransactionCoverage


class BankTransactionCoverageRepository(BaseRepository[BankTransactionCoverage]):
    """Repository for mirrored transaction coverage windows."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, BankTransactionCoverage)

    @staticmethod
    def _coerce_uuid(value: str | UUID) -> str | UUID:
        if isinstance(value, UUID):
            return value
        try:
            return UUID(value)
        except ValueError:
            return value

    async def list_for_account(
        self,
        linked_account_id: str | UUID,
        *,
        provider: str = "mono",
        coverage_type: str = "full",
    ) -> list[BankTransactionCoverage]:
        """Return merged coverage rows for one linked account."""
        lookup_id = self._coerce_uuid(linked_account_id)
        result = await self.db.execute(
            select(BankTransactionCoverage)
            .filter(
                BankTransactionCoverage.linked_account_id == lookup_id,
                BankTransactionCoverage.provider == provider,
                BankTransactionCoverage.coverage_type == coverage_type,
            )
            .order_by(BankTransactionCoverage.window_start.asc(), BankTransactionCoverage.window_end.asc())
        )
        return list(result.scalars().all())

    async def find_missing_gaps(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        coverage_type: str = "full",
    ) -> list[tuple[date, date]]:
        """Return uncovered date gaps within the requested window."""
        if start_date > end_date:
            return []

        coverages = await self.list_for_account(
            linked_account_id,
            provider=provider,
            coverage_type=coverage_type,
        )
        cursor = start_date
        gaps: list[tuple[date, date]] = []

        for coverage in coverages:
            if coverage.window_end < cursor:
                continue
            if coverage.window_start > end_date:
                break
            if coverage.window_start > cursor:
                gaps.append((cursor, min(end_date, coverage.window_start - timedelta(days=1))))
            cursor = max(cursor, coverage.window_end + timedelta(days=1))
            if cursor > end_date:
                break

        if cursor <= end_date:
            gaps.append((cursor, end_date))
        return gaps

    async def is_window_covered(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        coverage_type: str = "full",
    ) -> bool:
        """Return whether a requested window is fully covered."""
        missing = await self.find_missing_gaps(
            linked_account_id,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
            coverage_type=coverage_type,
        )
        return not missing

    async def add_full_coverage(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ) -> BankTransactionCoverage:
        """Insert a full-coverage window, merging adjacent or overlapping rows."""
        lookup_id = self._coerce_uuid(linked_account_id)
        merged_start = start_date
        merged_end = end_date
        overlap_start = start_date - timedelta(days=1)
        overlap_end = end_date + timedelta(days=1)
        result = await self.db.execute(
            select(BankTransactionCoverage).filter(
                BankTransactionCoverage.linked_account_id == lookup_id,
                BankTransactionCoverage.provider == provider,
                BankTransactionCoverage.coverage_type == "full",
                BankTransactionCoverage.window_end >= overlap_start,
                BankTransactionCoverage.window_start <= overlap_end,
            )
        )
        existing = list(result.scalars().all())
        for coverage in existing:
            merged_start = min(merged_start, coverage.window_start)
            merged_end = max(merged_end, coverage.window_end)

        if existing:
            await self.db.execute(
                delete(BankTransactionCoverage).where(
                    BankTransactionCoverage.id.in_([coverage.id for coverage in existing])
                )
            )

        coverage = BankTransactionCoverage(
            linked_account_id=lookup_id,
            provider=provider,
            window_start=merged_start,
            window_end=merged_end,
            coverage_type="full",
        )
        self.db.add(coverage)
        await self.db.flush()
        return coverage
