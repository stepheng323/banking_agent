"""Repository for single-transaction Mono debit steps."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.enums import TransactionDebitStepStatusEnum, TransactionStatusEnum, TransactionTypeEnum
from shared.database.models import Transaction, TransactionDebitStep


class TransactionDebitStepRepository(BaseRepository[TransactionDebitStep]):
    """Repository for transaction-owned debit/refund state transitions."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, TransactionDebitStep)

    @staticmethod
    def _coerce_id(value: str) -> UUID | str:
        try:
            return UUID(value)
        except ValueError:
            return value

    async def get_by_transaction(self, transaction_id: str) -> TransactionDebitStep | None:
        """Return the debit step for a transaction."""
        result = await self.db.execute(
            select(TransactionDebitStep).filter(
                TransactionDebitStep.transaction_id == self._coerce_id(transaction_id)
            )
        )
        return result.scalars().first()

    async def get_by_transaction_for_update(self, transaction_id: str) -> TransactionDebitStep | None:
        """Return and lock the debit step for a transaction."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .filter(TransactionDebitStep.transaction_id == self._coerce_id(transaction_id))
            .with_for_update()
        )
        return result.scalars().first()

    async def get_by_id_for_update(self, step_id: str) -> TransactionDebitStep | None:
        """Return and lock the debit step by ID."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .filter(TransactionDebitStep.id == self._coerce_id(step_id))
            .with_for_update()
        )
        return result.scalars().first()

    async def get_by_provider_reference_for_update(self, reference: str) -> TransactionDebitStep | None:
        """Return and lock a step by Mono provider reference."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .filter(TransactionDebitStep.provider_reference == reference)
            .with_for_update()
        )
        return result.scalars().first()

    async def get_by_provider_debit_id_for_update(self, debit_id: str) -> TransactionDebitStep | None:
        """Return and lock a step by Mono debit identifier."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .filter(TransactionDebitStep.provider_debit_id == debit_id)
            .with_for_update()
        )
        return result.scalars().first()

    async def get_or_create_for_transaction(
        self,
        *,
        transaction: Transaction,
        account_id: str,
        provider_reference: str,
    ) -> tuple[TransactionDebitStep, bool]:
        """Get or create the single debit step for an airtime/data transaction."""
        existing = await self.get_by_transaction(str(transaction.id))
        if existing:
            return existing, False

        step = TransactionDebitStep(
            transaction_id=transaction.id,
            account_id=self._coerce_id(account_id),
            amount=transaction.amount,
            currency=transaction.currency,
            provider_name="mono",
            provider_reference=provider_reference,
            status=TransactionDebitStepStatusEnum.PENDING.value,
        )
        self.db.add(step)
        await self.db.flush()
        return step, True

    async def claim_for_debit(
        self,
        step_id: str,
        *,
        provider_reference: str,
    ) -> TransactionDebitStep | None:
        """Atomically claim a pending transaction debit before calling Mono."""
        step = await self.get_by_id_for_update(step_id)
        if not step or step.status != TransactionDebitStepStatusEnum.PENDING.value:
            return None

        now = datetime.now(UTC).replace(tzinfo=None)
        step.status = TransactionDebitStepStatusEnum.PROCESSING.value
        step.provider_reference = step.provider_reference or provider_reference
        step.provider_name = step.provider_name or "mono"
        step.initiated_at = step.initiated_at or now
        self.db.add(step)
        await self.db.flush()
        return step

    async def claim_for_refund(
        self,
        step_id: str,
        *,
        refund_reference: str,
    ) -> TransactionDebitStep | None:
        """Atomically claim a confirmed/refund-pending debit before calling Mono refund."""
        step = await self.get_by_id_for_update(step_id)
        if not step:
            return None
        if step.status not in (
            TransactionDebitStepStatusEnum.CONFIRMED.value,
            TransactionDebitStepStatusEnum.REFUND_PENDING.value,
        ):
            return None
        if step.refund_provider_id or step.refund_initiated_at:
            return None

        step.status = TransactionDebitStepStatusEnum.REFUND_PROCESSING.value
        step.refund_provider_reference = step.refund_provider_reference or refund_reference
        step.refund_initiated_at = datetime.now(UTC).replace(tzinfo=None)
        self.db.add(step)
        await self.db.flush()
        return step

    async def update_status(
        self,
        step_id: str,
        status: str,
        *,
        provider_reference: str | None = None,
        provider_debit_id: str | None = None,
        error_message: str | None = None,
    ) -> TransactionDebitStep | None:
        """Update status and timestamp fields for a debit step."""
        step = await self.get_by_id(step_id)
        if not step:
            return None

        step.status = status
        if provider_reference:
            step.provider_reference = provider_reference
        if provider_debit_id:
            step.provider_debit_id = provider_debit_id
        if error_message:
            step.error_message = error_message

        now = datetime.now(UTC).replace(tzinfo=None)
        if status == TransactionDebitStepStatusEnum.PROCESSING.value:
            step.initiated_at = step.initiated_at or now
        elif status == TransactionDebitStepStatusEnum.CONFIRMED.value:
            step.confirmed_at = step.confirmed_at or now
        elif status == TransactionDebitStepStatusEnum.FAILED.value:
            step.failed_at = step.failed_at or now
        elif status == TransactionDebitStepStatusEnum.REFUNDED.value:
            step.refunded_at = step.refunded_at or now
        elif status in {
            TransactionDebitStepStatusEnum.REFUND_PENDING.value,
            TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
        }:
            step.refund_initiated_at = step.refund_initiated_at or now

        self.db.add(step)
        await self.db.flush()
        return step

    async def get_recoverable_open(self, *, cutoff: datetime, limit: int) -> list[TransactionDebitStep]:
        """Get stale pending/processing transaction debit steps."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .join(Transaction, Transaction.id == TransactionDebitStep.transaction_id)
            .filter(
                Transaction.transaction_type.in_(
                    [TransactionTypeEnum.AIRTIME.value, TransactionTypeEnum.DATA.value]
                ),
                Transaction.status.in_(
                    [TransactionStatusEnum.PENDING.value, TransactionStatusEnum.PROCESSING.value]
                ),
                or_(
                    and_(
                        TransactionDebitStep.status == TransactionDebitStepStatusEnum.PENDING.value,
                        Transaction.created_at <= cutoff,
                    ),
                    and_(
                        TransactionDebitStep.status == TransactionDebitStepStatusEnum.PROCESSING.value,
                        or_(
                            TransactionDebitStep.initiated_at.is_(None),
                            TransactionDebitStep.initiated_at <= cutoff,
                        ),
                    ),
                ),
            )
            .order_by(TransactionDebitStep.initiated_at.asc().nullsfirst(), Transaction.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_confirmed_without_success(self, *, limit: int) -> list[TransactionDebitStep]:
        """Get confirmed debits whose bill fulfillment has not closed the transaction."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .join(Transaction, Transaction.id == TransactionDebitStep.transaction_id)
            .filter(
                TransactionDebitStep.status == TransactionDebitStepStatusEnum.CONFIRMED.value,
                Transaction.transaction_type.in_(
                    [TransactionTypeEnum.AIRTIME.value, TransactionTypeEnum.DATA.value]
                ),
                Transaction.status.in_(
                    [TransactionStatusEnum.PENDING.value, TransactionStatusEnum.PROCESSING.value]
                ),
            )
            .order_by(TransactionDebitStep.confirmed_at.asc().nullsfirst())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_stale_refunds(self, *, cutoff: datetime, limit: int) -> list[TransactionDebitStep]:
        """Get transaction debit refunds waiting for provider confirmation."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .filter(
                TransactionDebitStep.status.in_(
                    [
                        TransactionDebitStepStatusEnum.REFUND_PENDING.value,
                        TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
                    ]
                ),
                or_(
                    TransactionDebitStep.refund_last_checked_at.is_(None),
                    TransactionDebitStep.refund_last_checked_at <= cutoff,
                ),
            )
            .order_by(
                TransactionDebitStep.refund_last_checked_at.asc().nullsfirst(),
                TransactionDebitStep.refund_initiated_at.asc(),
            )
            .limit(limit)
        )
        return list(result.scalars().all())
