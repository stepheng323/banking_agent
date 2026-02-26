"""Repository for FundedTransfer model."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.enums import FundedTransferStatusEnum
from shared.database.models import FundedTransfer
from shared.repositories.base import BaseRepository


class FundedTransferRepository(BaseRepository[FundedTransfer]):
    """Repository for FundedTransfer operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, FundedTransfer)

    async def get_by_user(self, user_id: str, limit: int = 20) -> list[FundedTransfer]:
        """Get all funded transfers for a user, ordered by created_at descending."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

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

    async def get_by_status(self, status: str) -> list[FundedTransfer]:
        """Get funded transfers by status (for background processing)."""
        result = await self.db.execute(
            select(FundedTransfer)
            .filter(FundedTransfer.status == status)
            .order_by(FundedTransfer.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_pending_funding(self) -> list[FundedTransfer]:
        """Get transfers waiting for funding to complete."""
        return await self.get_by_status(FundedTransferStatusEnum.FUNDING_PENDING.value)

    async def get_pending_payout(self) -> list[FundedTransfer]:
        """Get transfers ready for payout."""
        return await self.get_by_status(FundedTransferStatusEnum.PAYOUT_PENDING.value)

    async def get_refunding(self) -> list[FundedTransfer]:
        """Get transfers being refunded."""
        return await self.get_by_status(FundedTransferStatusEnum.REFUNDING.value)

    async def update_status(self, transfer_id: str, status: str, error_message: str | None = None) -> FundedTransfer | None:
        """Update transfer status."""
        transfer = await self.get_by_id(transfer_id)
        if transfer:
            transfer.status = status
            if error_message:
                transfer.error_message = error_message
            self.db.add(transfer)
            await self.db.flush()
        return transfer
