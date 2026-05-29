"""Repository for FundedTransfer model."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.enums import FundedTransferStatusEnum
from shared.database.models import FundedTransfer
from banking.persistence.base import BaseRepository


class FundedTransferRepository(BaseRepository[FundedTransfer]):
    """Repository for FundedTransfer operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, FundedTransfer)

    @staticmethod
    def _coerce_user_id(user_id: str) -> UUID | str:
        try:
            return UUID(user_id)
        except ValueError:
            return user_id

    @staticmethod
    def _coerce_transfer_id(transfer_id: str | None) -> UUID | None:
        if not transfer_id:
            return None
        try:
            return UUID(transfer_id)
        except ValueError:
            return None

    async def get_by_user(self, user_id: str, limit: int = 20) -> list[FundedTransfer]:
        """Get all funded transfers for a user, ordered by created_at descending."""
        lookup_id: UUID | str = self._coerce_user_id(user_id)

        result = await self.db.execute(
            select(FundedTransfer)
            .filter(FundedTransfer.user_id == lookup_id)
            .order_by(FundedTransfer.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_idempotency_key(self, idempotency_key: str) -> FundedTransfer | None:
        """Get a funded transfer by idempotency key."""
        result = await self.db.execute(select(FundedTransfer).filter(FundedTransfer.idempotency_key == idempotency_key))
        return result.scalars().first()

    async def get_by_id_for_update(self, transfer_id: str) -> FundedTransfer | None:
        """Get a funded transfer by ID and lock it for state transitions."""
        lookup_id = self._coerce_transfer_id(transfer_id) or transfer_id
        result = await self.db.execute(
            select(FundedTransfer).filter(FundedTransfer.id == lookup_id).with_for_update()
        )
        return result.scalars().first()

    async def get_by_payout_reference(self, payout_reference: str) -> FundedTransfer | None:
        """Get a funded transfer by provider payout reference."""
        result = await self.db.execute(
            select(FundedTransfer).filter(FundedTransfer.payout_reference == payout_reference)
        )
        return result.scalars().first()

    async def get_by_status(self, status: str) -> list[FundedTransfer]:
        """Get funded transfers by status (for background processing)."""
        result = await self.db.execute(
            select(FundedTransfer).filter(FundedTransfer.status == status).order_by(FundedTransfer.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_pending_funding(self) -> list[FundedTransfer]:
        """Get transfers waiting for funding to complete."""
        return await self.get_by_status(FundedTransferStatusEnum.FUNDING_PENDING.value)

    async def get_pending_payout(self) -> list[FundedTransfer]:
        """Get transfers ready for payout."""
        return await self.get_by_status(FundedTransferStatusEnum.PAYOUT_PENDING.value)

    async def get_stale_pending_payout(self, *, cutoff: datetime, limit: int = 50) -> list[FundedTransfer]:
        """Get payout-pending transfers old enough for reconciliation."""
        result = await self.db.execute(
            select(FundedTransfer)
            .filter(
                FundedTransfer.status == FundedTransferStatusEnum.PAYOUT_PENDING.value,
                or_(FundedTransfer.payout_initiated_at.is_(None), FundedTransfer.payout_initiated_at <= cutoff),
            )
            .order_by(FundedTransfer.payout_initiated_at.asc().nullsfirst(), FundedTransfer.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim_for_payout(self, transfer_id: str, *, payout_reference: str) -> FundedTransfer | None:
        """Atomically claim a payout-pending transfer before calling payout provider."""
        transfer = await self.get_by_id_for_update(transfer_id)
        if not transfer or transfer.status != FundedTransferStatusEnum.PAYOUT_PENDING.value:
            return None
        if transfer.payout_initiated_at:
            return None

        transfer.payout_reference = transfer.payout_reference or payout_reference
        transfer.payout_initiated_at = datetime.now(UTC).replace(tzinfo=None)
        self.db.add(transfer)
        await self.db.flush()
        return transfer

    async def get_refunding(self) -> list[FundedTransfer]:
        """Get transfers being refunded."""
        return await self.get_by_status(FundedTransferStatusEnum.REFUNDING.value)

    async def has_prior_completed_pooled_transfer(
        self,
        user_id: str,
        *,
        exclude_transfer_id: str | None = None,
        exclude_idempotency_key: str | None = None,
    ) -> bool:
        """Return whether the user has a previous completed multi-account transfer."""
        lookup_id = self._coerce_user_id(user_id)
        filters = [
            FundedTransfer.user_id == lookup_id,
            FundedTransfer.status == FundedTransferStatusEnum.COMPLETED.value,
        ]
        excluded_id = self._coerce_transfer_id(exclude_transfer_id)
        if excluded_id is not None:
            filters.append(FundedTransfer.id != excluded_id)
        if exclude_idempotency_key:
            filters.append(FundedTransfer.idempotency_key != exclude_idempotency_key)

        result = await self.db.execute(select(FundedTransfer.id).filter(*filters).limit(1))
        return result.scalars().first() is not None

    async def update_status(
        self, transfer_id: str, status: str, error_message: str | None = None
    ) -> FundedTransfer | None:
        """Update transfer status."""
        transfer = await self.get_by_id(transfer_id)
        if transfer:
            transfer.status = status
            if error_message:
                transfer.error_message = error_message
            self.db.add(transfer)
            await self.db.flush()
        return transfer
