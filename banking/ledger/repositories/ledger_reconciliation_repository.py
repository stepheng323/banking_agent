"""Repositories for ledger reconciliation scans and findings."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.enums import (
    FundedTransferStatusEnum,
    FundingStepStatusEnum,
    TransactionDebitStepStatusEnum,
    TransactionStatusEnum,
    TransactionTypeEnum,
)
from shared.database.models import (
    FundedTransfer,
    FundingStep,
    LedgerReconciliationFinding,
    LedgerReconciliationRun,
    Transaction,
    TransactionDebitStep,
)
from shared.money import to_naira
from shared.observability.events import emit_operational_event
from shared.utils.json import to_json_safe_dict


class LedgerReconciliationRepository:
    """Repository for ledger reconciliation work lists and findings."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_run(self, *, run_type: str, details: dict[str, Any] | None = None) -> LedgerReconciliationRun:
        """Create a reconciliation run record."""
        run = LedgerReconciliationRun(run_type=run_type, details=to_json_safe_dict(details or {}))
        self.db.add(run)
        await self.db.flush()
        return run

    async def finish_run(
        self,
        run: LedgerReconciliationRun,
        *,
        status: str,
        scanned_count: int,
        repaired_count: int = 0,
        finding_count: int = 0,
        error_message: str | None = None,
    ) -> LedgerReconciliationRun:
        """Mark a reconciliation run completed or failed."""
        run.status = status
        run.finished_at = datetime.now(UTC).replace(tzinfo=None)
        run.scanned_count = scanned_count
        run.repaired_count = repaired_count
        run.finding_count = finding_count
        run.error_message = error_message
        self.db.add(run)
        await self.db.flush()
        return run

    async def list_confirmed_funding_steps(self, *, limit: int) -> list[FundingStep]:
        """List funding steps whose Mono debit was or should be confirmed in the ledger."""
        result = await self.db.execute(
            select(FundingStep)
            .filter(
                FundingStep.status.in_(
                    [
                        FundingStepStatusEnum.CONFIRMED.value,
                        FundingStepStatusEnum.REFUND_PENDING.value,
                        FundingStepStatusEnum.REFUND_PROCESSING.value,
                        FundingStepStatusEnum.REFUND_FAILED.value,
                        FundingStepStatusEnum.REFUNDED.value,
                    ]
                )
            )
            .order_by(FundingStep.confirmed_at.asc().nullsfirst(), FundingStep.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_refunded_funding_steps(self, *, limit: int) -> list[FundingStep]:
        """List terminal refunded funding steps for posting reconciliation."""
        result = await self.db.execute(
            select(FundingStep)
            .filter(FundingStep.status == FundingStepStatusEnum.REFUNDED.value)
            .order_by(FundingStep.refunded_at.asc().nullsfirst(), FundingStep.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_completed_funded_transfers(self, *, limit: int) -> list[FundedTransfer]:
        """List completed pooled transfers for payout posting reconciliation."""
        result = await self.db.execute(
            select(FundedTransfer)
            .filter(FundedTransfer.status == FundedTransferStatusEnum.COMPLETED.value)
            .order_by(FundedTransfer.completed_at.asc().nullsfirst(), FundedTransfer.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_successful_non_pooled_transactions(self, *, limit: int) -> list[Transaction]:
        """List successful transactions that should have transaction-level ledger entries."""
        result = await self.db.execute(
            select(Transaction)
            .outerjoin(FundedTransfer, FundedTransfer.idempotency_key == Transaction.idempotency_key)
            .outerjoin(TransactionDebitStep, TransactionDebitStep.transaction_id == Transaction.id)
            .filter(
                Transaction.status == TransactionStatusEnum.SUCCESSFUL.value,
                Transaction.transaction_type.in_(
                    [
                        TransactionTypeEnum.TRANSFER.value,
                        TransactionTypeEnum.AIRTIME.value,
                        TransactionTypeEnum.DATA.value,
                        TransactionTypeEnum.BILL.value,
                    ]
                ),
                FundedTransfer.id.is_(None),
                TransactionDebitStep.id.is_(None),
            )
            .order_by(Transaction.completed_at.asc().nullsfirst(), Transaction.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_confirmed_transaction_debit_steps(self, *, limit: int) -> list[TransactionDebitStep]:
        """List confirmed single-transaction debits that require debit ledger entries."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .filter(
                TransactionDebitStep.status.in_(
                    [
                        TransactionDebitStepStatusEnum.CONFIRMED.value,
                        TransactionDebitStepStatusEnum.REFUND_PENDING.value,
                        TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
                        TransactionDebitStepStatusEnum.REFUND_FAILED.value,
                        TransactionDebitStepStatusEnum.REFUNDED.value,
                    ]
                )
            )
            .order_by(TransactionDebitStep.confirmed_at.asc().nullsfirst())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_successful_debit_backed_bill_transactions(self, *, limit: int) -> list[Transaction]:
        """List successful airtime/data transactions backed by transaction debit steps."""
        result = await self.db.execute(
            select(Transaction)
            .join(TransactionDebitStep, TransactionDebitStep.transaction_id == Transaction.id)
            .filter(
                Transaction.status == TransactionStatusEnum.SUCCESSFUL.value,
                Transaction.transaction_type.in_([TransactionTypeEnum.AIRTIME.value, TransactionTypeEnum.DATA.value]),
                TransactionDebitStep.status == TransactionDebitStepStatusEnum.CONFIRMED.value,
            )
            .order_by(Transaction.completed_at.asc().nullsfirst(), Transaction.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_refunded_transaction_debit_steps(self, *, limit: int) -> list[TransactionDebitStep]:
        """List refunded single-transaction debit steps that require refund ledger entries."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .filter(TransactionDebitStep.status == TransactionDebitStepStatusEnum.REFUNDED.value)
            .order_by(TransactionDebitStep.refunded_at.asc().nullsfirst())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_transfers_for_exposure_scan(self, *, cutoff: datetime, limit: int) -> list[FundedTransfer]:
        """List pooled transfers old enough for exposure reconciliation."""
        result = await self.db.execute(
            select(FundedTransfer)
            .filter(
                FundedTransfer.status.in_(
                    [
                        FundedTransferStatusEnum.COMPLETED.value,
                        FundedTransferStatusEnum.REFUNDED.value,
                        FundedTransferStatusEnum.FAILED.value,
                        FundedTransferStatusEnum.REFUNDING.value,
                    ]
                ),
                or_(FundedTransfer.created_at <= cutoff, FundedTransfer.completed_at <= cutoff),
            )
            .order_by(FundedTransfer.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_transaction_debits_for_exposure_scan(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[TransactionDebitStep]:
        """List debit-backed bill steps old enough for transaction exposure reconciliation."""
        result = await self.db.execute(
            select(TransactionDebitStep)
            .join(Transaction, Transaction.id == TransactionDebitStep.transaction_id)
            .filter(
                Transaction.transaction_type.in_([TransactionTypeEnum.AIRTIME.value, TransactionTypeEnum.DATA.value]),
                TransactionDebitStep.status.in_(
                    [
                        TransactionDebitStepStatusEnum.CONFIRMED.value,
                        TransactionDebitStepStatusEnum.REFUND_PENDING.value,
                        TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
                        TransactionDebitStepStatusEnum.REFUND_FAILED.value,
                        TransactionDebitStepStatusEnum.REFUNDED.value,
                    ]
                ),
                or_(
                    Transaction.updated_at <= cutoff,
                    TransactionDebitStep.confirmed_at <= cutoff,
                    TransactionDebitStep.refund_initiated_at <= cutoff,
                    TransactionDebitStep.refunded_at <= cutoff,
                ),
            )
            .order_by(Transaction.updated_at.asc(), TransactionDebitStep.confirmed_at.asc().nullsfirst())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_direct_transfer_transactions_for_exposure_scan(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[Transaction]:
        """List successful direct-transfer transactions old enough for ledger exposure checks."""
        result = await self.db.execute(
            select(Transaction)
            .outerjoin(FundedTransfer, FundedTransfer.idempotency_key == Transaction.idempotency_key)
            .filter(
                Transaction.transaction_type == TransactionTypeEnum.TRANSFER.value,
                Transaction.status == TransactionStatusEnum.SUCCESSFUL.value,
                FundedTransfer.id.is_(None),
                or_(Transaction.completed_at <= cutoff, Transaction.updated_at <= cutoff),
            )
            .order_by(Transaction.completed_at.asc().nullsfirst(), Transaction.updated_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_finding_by_key(self, finding_key: str) -> LedgerReconciliationFinding | None:
        """Return a reconciliation finding by stable key."""
        result = await self.db.execute(
            select(LedgerReconciliationFinding).filter(LedgerReconciliationFinding.finding_key == finding_key)
        )
        return result.scalars().first()

    async def open_or_refresh_finding(
        self,
        *,
        finding_key: str,
        severity: str,
        finding_type: str,
        transaction_id: UUID | None = None,
        funded_transfer_id: UUID | None = None,
        funding_step_id: UUID | None = None,
        expected_amount_naira: Decimal | None = None,
        actual_amount_naira: Decimal | None = None,
        details: dict[str, Any] | None = None,
    ) -> LedgerReconciliationFinding:
        """Create or update an open reconciliation finding."""
        now = datetime.now(UTC).replace(tzinfo=None)
        finding = await self.get_finding_by_key(finding_key)
        should_emit_open = finding is None or finding.status == "resolved"
        if not finding:
            finding = LedgerReconciliationFinding(
                finding_key=finding_key,
                severity=severity,
                finding_type=finding_type,
                transaction_id=transaction_id,
                funded_transfer_id=funded_transfer_id,
                funding_step_id=funding_step_id,
                expected_amount_naira=to_naira(expected_amount_naira) if expected_amount_naira is not None else None,
                actual_amount_naira=to_naira(actual_amount_naira) if actual_amount_naira is not None else None,
                details=to_json_safe_dict(details or {}),
                first_seen_at=now,
                last_seen_at=now,
            )
        else:
            finding.status = "open"
            finding.severity = severity
            finding.finding_type = finding_type
            finding.transaction_id = transaction_id or finding.transaction_id
            finding.funded_transfer_id = funded_transfer_id or finding.funded_transfer_id
            finding.funding_step_id = funding_step_id or finding.funding_step_id
            finding.expected_amount_naira = (
                to_naira(expected_amount_naira) if expected_amount_naira is not None else None
            )
            finding.actual_amount_naira = to_naira(actual_amount_naira) if actual_amount_naira is not None else None
            finding.details = to_json_safe_dict(details or {})
            finding.last_seen_at = now
            finding.resolved_at = None
        self.db.add(finding)
        await self.db.flush()
        if should_emit_open:
            emit_operational_event(
                "ledger_reconciliation_finding_opened",
                severity="critical" if severity == "critical" else "high" if severity == "high" else "warning",
                domain="ledger",
                identifiers={
                    "finding_key": finding_key,
                    "transaction_id": transaction_id,
                    "funded_transfer_id": funded_transfer_id,
                    "funding_step_id": funding_step_id,
                },
                details={
                    "finding_type": finding_type,
                    "severity": severity,
                    "expected_amount_naira": expected_amount_naira,
                    "actual_amount_naira": actual_amount_naira,
                },
            )
        return finding

    async def resolve_finding(self, finding_key: str) -> LedgerReconciliationFinding | None:
        """Resolve an open finding when the mismatch is no longer true."""
        finding = await self.get_finding_by_key(finding_key)
        if not finding or finding.status == "resolved":
            return finding
        finding.status = "resolved"
        finding.resolved_at = datetime.now(UTC).replace(tzinfo=None)
        self.db.add(finding)
        await self.db.flush()
        emit_operational_event(
            "ledger_reconciliation_finding_resolved",
            severity="info",
            domain="ledger",
            identifiers={
                "finding_key": finding.finding_key,
                "transaction_id": finding.transaction_id,
                "funded_transfer_id": finding.funded_transfer_id,
                "funding_step_id": finding.funding_step_id,
            },
            details={"finding_type": finding.finding_type, "severity": finding.severity},
        )
        return finding
