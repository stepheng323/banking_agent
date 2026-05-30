"""Repository for FundingStep model."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.enums import FundingStepStatusEnum
from shared.database.models import FundedTransfer, FundingStep


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

    async def get_by_id_for_update(self, step_id: str) -> FundingStep | None:
        """Get a funding step by ID and lock it for a state transition."""
        lookup_id: UUID | str = step_id
        try:
            lookup_id = UUID(step_id)
        except ValueError:
            pass

        result = await self.db.execute(select(FundingStep).filter(FundingStep.id == lookup_id).with_for_update())
        return result.scalars().first()

    async def get_by_provider_reference(self, reference: str) -> FundingStep | None:
        """Get a funding step by provider reference (for webhook handling)."""
        result = await self.db.execute(select(FundingStep).filter(FundingStep.provider_reference == reference))
        return result.scalars().first()

    async def get_by_provider_reference_for_update(self, reference: str) -> FundingStep | None:
        """Get a funding step by provider reference and lock it for webhook handling."""
        result = await self.db.execute(
            select(FundingStep).filter(FundingStep.provider_reference == reference).with_for_update()
        )
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

    async def get_stale_processing(self, *, cutoff: datetime, limit: int = 50) -> list[FundingStep]:
        """Get funding steps waiting too long for debit confirmation."""
        result = await self.db.execute(
            select(FundingStep)
            .filter(
                FundingStep.status.in_(
                    [
                        FundingStepStatusEnum.PROCESSING.value,
                    ]
                ),
                or_(FundingStep.initiated_at.is_(None), FundingStep.initiated_at <= cutoff),
            )
            .order_by(FundingStep.initiated_at.asc().nullsfirst(), FundingStep.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recoverable_open(self, *, cutoff: datetime, limit: int = 50) -> list[FundingStep]:
        """Get pending/processing funding steps old enough for reconciliation recovery."""
        result = await self.db.execute(
            select(FundingStep)
            .join(FundedTransfer, FundedTransfer.id == FundingStep.funded_transfer_id)
            .filter(
                or_(
                    and_(
                        FundingStep.status == FundingStepStatusEnum.PENDING.value,
                        FundedTransfer.created_at <= cutoff,
                    ),
                    and_(
                        FundingStep.status == FundingStepStatusEnum.PROCESSING.value,
                        or_(FundingStep.initiated_at.is_(None), FundingStep.initiated_at <= cutoff),
                    ),
                ),
            )
            .order_by(FundingStep.initiated_at.asc().nullsfirst(), FundingStep.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim_for_debit(self, step_id: str, *, provider_reference: str) -> FundingStep | None:
        """Atomically claim a pending step before calling the debit provider."""
        step = await self.get_by_id_for_update(step_id)
        if not step or step.status != FundingStepStatusEnum.PENDING.value:
            return None

        now = datetime.now(UTC).replace(tzinfo=None)
        step.status = FundingStepStatusEnum.PROCESSING.value
        step.provider_reference = step.provider_reference or provider_reference
        step.initiated_at = step.initiated_at or now
        self.db.add(step)
        await self.db.flush()
        return step

    async def claim_for_refund(
        self,
        step_id: str,
        *,
        refund_reference: str,
    ) -> FundingStep | None:
        """Atomically claim a confirmed/refund-pending step before calling Mono refund."""
        step = await self.get_by_id_for_update(step_id)
        if not step:
            return None
        if step.status not in (
            FundingStepStatusEnum.CONFIRMED.value,
            FundingStepStatusEnum.REFUND_PENDING.value,
        ):
            return None
        if step.refund_provider_id or step.refund_initiated_at:
            return None

        now = datetime.now(UTC).replace(tzinfo=None)
        step.status = FundingStepStatusEnum.REFUND_PROCESSING.value
        step.refund_provider_reference = step.refund_provider_reference or refund_reference
        step.refund_initiated_at = now
        self.db.add(step)
        await self.db.flush()
        return step

    async def apply_debit_result_if_open(
        self,
        step_id: str,
        *,
        status: str,
        provider_reference: str | None = None,
        provider_debit_id: str | None = None,
        error_message: str | None = None,
    ) -> FundingStep | None:
        """Apply a debit provider result only while the step is still open."""
        step = await self.get_by_id_for_update(step_id)
        if not step:
            return None
        if step.status not in (FundingStepStatusEnum.PENDING.value, FundingStepStatusEnum.PROCESSING.value):
            return step
        return await self.update_status(
            step_id,
            status,
            provider_reference=provider_reference,
            provider_debit_id=provider_debit_id,
            error_message=error_message,
        )

    async def get_stale_refunds(self, *, cutoff: datetime, limit: int = 50) -> list[FundingStep]:
        """Get refund steps waiting too long for refund confirmation."""
        result = await self.db.execute(
            select(FundingStep)
            .filter(
                FundingStep.status.in_(
                    [
                        FundingStepStatusEnum.REFUND_PENDING.value,
                        FundingStepStatusEnum.REFUND_PROCESSING.value,
                    ]
                ),
                or_(
                    FundingStep.refund_last_checked_at.is_(None),
                    FundingStep.refund_last_checked_at <= cutoff,
                ),
            )
            .order_by(FundingStep.refund_last_checked_at.asc().nullsfirst(), FundingStep.refund_initiated_at.asc())
            .limit(limit)
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
        provider_reference: str | None = None,
        provider_debit_id: str | None = None,
        error_message: str | None = None,
    ) -> FundingStep | None:
        """Update funding step status and provider details."""
        step = await self.get_by_id(step_id)
        if step:
            step.status = status
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
            elif status in (
                FundingStepStatusEnum.REFUND_PENDING.value,
                FundingStepStatusEnum.REFUND_PROCESSING.value,
            ):
                if not step.refund_initiated_at:
                    step.refund_initiated_at = now
            elif status == FundingStepStatusEnum.REFUND_FAILED.value:
                step.failed_at = now

            self.db.add(step)
            await self.db.flush()
        return step

    async def update_refund_tracking(
        self,
        step_id: str,
        *,
        status: str | None = None,
        refund_provider_id: str | None = None,
        refund_provider_reference: str | None = None,
        refund_error_message: str | None = None,
        increment_attempt: bool = False,
    ) -> FundingStep | None:
        """Update refund provider metadata for a funding step."""
        step = await self.get_by_id(step_id)
        if not step:
            return None

        now = datetime.now(UTC).replace(tzinfo=None)
        if status:
            step.status = status
            if status in (
                FundingStepStatusEnum.REFUND_PENDING.value,
                FundingStepStatusEnum.REFUND_PROCESSING.value,
            ) and not step.refund_initiated_at:
                step.refund_initiated_at = now
            elif status == FundingStepStatusEnum.REFUNDED.value:
                step.refunded_at = now
            elif status == FundingStepStatusEnum.REFUND_FAILED.value:
                step.failed_at = now
        if refund_provider_id:
            step.refund_provider_id = refund_provider_id
        if refund_provider_reference:
            step.refund_provider_reference = refund_provider_reference
        if refund_error_message:
            step.refund_error_message = refund_error_message
        if increment_attempt:
            step.refund_attempt_count = int(step.refund_attempt_count or 0) + 1
        step.refund_last_checked_at = now

        self.db.add(step)
        await self.db.flush()
        return step
