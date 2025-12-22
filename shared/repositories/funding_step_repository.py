"""Repository for FundingStep model."""
from uuid import UUID
from typing import List, Optional
from sqlalchemy.orm import Session

from shared.repositories.base import BaseRepository
from shared.database.models import FundingStep, FundingStepStatusEnum


class FundingStepRepository(BaseRepository[FundingStep]):
    """Repository for FundingStep operations."""

    def __init__(self, db: Session):
        super().__init__(db, FundingStep)

    def get_by_transfer(self, funded_transfer_id: str) -> List[FundingStep]:
        """Get all funding steps for a transfer, ordered by sequence."""
        if isinstance(funded_transfer_id, str):
            try:
                funded_transfer_id = UUID(funded_transfer_id)
            except ValueError:
                pass
        return (
            self.db.query(FundingStep)
            .filter(FundingStep.funded_transfer_id == funded_transfer_id)
            .order_by(FundingStep.sequence.asc())
            .all()
        )

    def get_by_provider_reference(self, reference: str) -> Optional[FundingStep]:
        """Get a funding step by provider reference (for webhook handling)."""
        return (
            self.db.query(FundingStep)
            .filter(FundingStep.provider_reference == reference)
            .first()
        )

    def get_by_provider_debit_id(self, debit_id: str) -> Optional[FundingStep]:
        """Get a funding step by provider debit ID."""
        return (
            self.db.query(FundingStep)
            .filter(FundingStep.provider_debit_id == debit_id)
            .first()
        )

    def get_pending_for_transfer(self, funded_transfer_id: str) -> List[FundingStep]:
        """Get pending funding steps for a transfer."""
        if isinstance(funded_transfer_id, str):
            try:
                funded_transfer_id = UUID(funded_transfer_id)
            except ValueError:
                pass
        return (
            self.db.query(FundingStep)
            .filter(
                FundingStep.funded_transfer_id == funded_transfer_id,
                FundingStep.status.in_([
                    FundingStepStatusEnum.PENDING.value,
                    FundingStepStatusEnum.PROCESSING.value
                ])
            )
            .order_by(FundingStep.sequence.asc())
            .all()
        )

    def get_confirmed_for_transfer(self, funded_transfer_id: str) -> List[FundingStep]:
        """Get confirmed funding steps for a transfer."""
        if isinstance(funded_transfer_id, str):
            try:
                funded_transfer_id = UUID(funded_transfer_id)
            except ValueError:
                pass
        return (
            self.db.query(FundingStep)
            .filter(
                FundingStep.funded_transfer_id == funded_transfer_id,
                FundingStep.status == FundingStepStatusEnum.CONFIRMED.value
            )
            .all()
        )

    def all_confirmed(self, funded_transfer_id: str) -> bool:
        """Check if all funding steps for a transfer are confirmed."""
        steps = self.get_by_transfer(funded_transfer_id)
        if not steps:
            return False
        return all(s.status == FundingStepStatusEnum.CONFIRMED.value for s in steps)

    def any_failed(self, funded_transfer_id: str) -> bool:
        """Check if any funding step for a transfer has failed."""
        if isinstance(funded_transfer_id, str):
            try:
                funded_transfer_id = UUID(funded_transfer_id)
            except ValueError:
                pass
        return (
            self.db.query(FundingStep)
            .filter(
                FundingStep.funded_transfer_id == funded_transfer_id,
                FundingStep.status == FundingStepStatusEnum.FAILED.value
            )
            .first() is not None
        )

    def get_by_transfer_id(self, transfer_id: str) -> List[FundingStep]:
        """Alias for get_by_transfer (used by webhook handler)."""
        return self.get_by_transfer(transfer_id)

    def are_all_confirmed(self, transfer_id: str) -> bool:
        """Alias for all_confirmed (used by webhook handler)."""
        return self.all_confirmed(transfer_id)

    def update_status(
        self,
        step_id: str,
        status: str,
        provider_response: dict | None = None,
    ) -> Optional[FundingStep]:
        """Update funding step status and provider response."""
        if isinstance(step_id, str):
            try:
                step_id = UUID(step_id)
            except ValueError:
                return None
        
        step = self.db.query(FundingStep).filter(FundingStep.id == step_id).first()
        if step:
            step.status = status
            if provider_response:
                step.provider_response = provider_response
            return step
        return None

