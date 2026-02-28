"""Repository for FundingStep model."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.enums import FundingStepStatusEnum
from shared.database.models import FundingStep
from shared.repositories.base import BaseRepository


class FundingStepRepository(BaseRepository[FundingStep]):
    """Repository for FundingStep operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, FundingStep)

    async def get_by_transfer(self, funded_transfer_id: str) -> list[FundingStep]:
        """Get all funding steps for a transfer, ordered by sequence."""
        lookup_id: UUID | str = funded_transfer_id
        try:
            lookup_id = UUID(funded_transfer_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(FundingStep).filter(FundingStep.funded_transfer_id == lookup_id).order_by(FundingStep.sequence.asc())
        )
        return list(result.scalars().all())

    async def get_by_provider_reference(self, reference: str) -> FundingStep | None:
        """Get a funding step by provider reference (for webhook handling)."""
        result = await self.db.execute(select(FundingStep).filter(FundingStep.provider_reference == reference))
        return result.scalars().first()

    async def get_by_provider_debit_id(self, debit_id: str) -> FundingStep | None:
        """Get a funding step by provider debit ID."""
        result = await self.db.execute(select(FundingStep).filter(FundingStep.provider_debit_id == debit_id))
        return result.scalars().first()

    async def get_pending_for_transfer(self, funded_transfer_id: str) -> list[FundingStep]:
        """Get pending funding steps for a transfer."""
        lookup_id: UUID | str = funded_transfer_id
        try:
            lookup_id = UUID(funded_transfer_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(FundingStep)
            .filter(
                FundingStep.funded_transfer_id == lookup_id,
                FundingStep.status.in_([FundingStepStatusEnum.PENDING.value, FundingStepStatusEnum.PROCESSING.value]),
            )
            .order_by(FundingStep.sequence.asc())
        )
        return list(result.scalars().all())

    async def get_confirmed_for_transfer(self, funded_transfer_id: str) -> list[FundingStep]:
        """Get confirmed funding steps for a transfer."""
        lookup_id: UUID | str = funded_transfer_id
        try:
            lookup_id = UUID(funded_transfer_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(FundingStep).filter(
                FundingStep.funded_transfer_id == lookup_id,
                FundingStep.status == FundingStepStatusEnum.CONFIRMED.value,
            )
        )
        return list(result.scalars().all())

    async def all_confirmed(self, funded_transfer_id: str) -> bool:
        """Check if all funding steps for a transfer are confirmed."""
        steps = await self.get_by_transfer(funded_transfer_id)
        if not steps:
            return False
        return all(s.status == FundingStepStatusEnum.CONFIRMED.value for s in steps)

    async def any_failed(self, funded_transfer_id: str) -> bool:
        """Check if any funding step for a transfer has failed."""
        lookup_id: UUID | str = funded_transfer_id
        try:
            lookup_id = UUID(funded_transfer_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(FundingStep).filter(
                FundingStep.funded_transfer_id == lookup_id,
                FundingStep.status == FundingStepStatusEnum.FAILED.value,
            )
        )
        return result.scalars().first() is not None

    async def update_status(
        self,
        step_id: str,
        status: str,
        provider_response: dict | None = None,
        provider_reference: str | None = None,
        provider_debit_id: str | None = None,
        error_message: str | None = None,
    ) -> FundingStep | None:
        """Update funding step status and provider details."""
        step = await self.get_by_id(step_id)
        if step:
            step.status = status
            # FundingStep currently has no provider_response JSON column.
            # We keep the argument for API compatibility, but only persist
            # normalized metadata fields (status, references, debit_id, errors).
            if provider_reference:
                step.provider_reference = provider_reference
            if provider_debit_id:
                step.provider_debit_id = provider_debit_id
            if error_message:
                step.error_message = error_message

            now = datetime.now(UTC).replace(tzinfo=None)
            if status == FundingStepStatusEnum.PROCESSING.value and not step.initiated_at:
                step.initiated_at = now
            elif status == FundingStepStatusEnum.CONFIRMED.value:
                step.confirmed_at = now
            elif status == FundingStepStatusEnum.FAILED.value:
                step.failed_at = now
            elif status == FundingStepStatusEnum.REFUNDED.value:
                step.refunded_at = now

            self.db.add(step)
            await self.db.flush()
        return step
