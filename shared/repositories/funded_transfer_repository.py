"""Repository for FundedTransfer model."""

from uuid import UUID

from sqlalchemy.orm import Session

from shared.database.models import FundedTransfer, FundedTransferStatusEnum
from shared.repositories.base import BaseRepository


class FundedTransferRepository(BaseRepository[FundedTransfer]):
    """Repository for FundedTransfer operations."""

    def __init__(self, db: Session):
        super().__init__(db, FundedTransfer)

    def get_by_user(self, user_id: str, limit: int = 20) -> list[FundedTransfer]:
        """Get all funded transfers for a user, ordered by created_at descending."""
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError:
                pass
        return (
            self.db.query(FundedTransfer)
            .filter(FundedTransfer.user_id == user_id)
            .order_by(FundedTransfer.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_idempotency_key(self, idempotency_key: str) -> FundedTransfer | None:
        """Get a funded transfer by idempotency key."""
        return (
            self.db.query(FundedTransfer)
            .filter(FundedTransfer.idempotency_key == idempotency_key)
            .first()
        )

    def get_by_status(self, status: str) -> list[FundedTransfer]:
        """Get funded transfers by status (for background processing)."""
        return (
            self.db.query(FundedTransfer)
            .filter(FundedTransfer.status == status)
            .order_by(FundedTransfer.created_at.asc())
            .all()
        )

    def get_pending_funding(self) -> list[FundedTransfer]:
        """Get transfers waiting for funding to complete."""
        return self.get_by_status(FundedTransferStatusEnum.FUNDING_PENDING.value)

    def get_pending_payout(self) -> list[FundedTransfer]:
        """Get transfers ready for payout."""
        return self.get_by_status(FundedTransferStatusEnum.PAYOUT_PENDING.value)

    def get_refunding(self) -> list[FundedTransfer]:
        """Get transfers being refunded."""
        return self.get_by_status(FundedTransferStatusEnum.REFUNDING.value)

    def update_status(self, transfer_id: str, status: str) -> FundedTransfer | None:
        """Update transfer status."""
        if isinstance(transfer_id, str):
            try:
                transfer_id = UUID(transfer_id)
            except ValueError:
                return None

        transfer = self.db.query(FundedTransfer).filter(FundedTransfer.id == transfer_id).first()
        if transfer:
            transfer.status = status
            return transfer
        return None
