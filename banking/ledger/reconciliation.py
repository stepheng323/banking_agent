"""Ledger reconciliation consumers for pooled-transfer accounting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from banking.ledger.errors import LedgerEntryConflict
from banking.ledger.posting import (
    DIRECT_TRANSFER_ENTRY_TYPE,
    FUNDING_ENTRY_TYPE,
    PAYOUT_ENTRY_TYPE,
    REFUND_ENTRY_TYPE,
    TRANSACTION_BILL_ENTRY_TYPE,
    TRANSACTION_DEBIT_ENTRY_TYPE,
    TRANSACTION_DEBIT_REFUND_ENTRY_TYPE,
    LedgerPostingService,
    transaction_success_entry_key,
)
from banking.persistence.unit_of_work import UnitOfWork
from shared.config.settings import settings
from shared.database.enums import (
    FundedTransferStatusEnum,
    FundingStepStatusEnum,
    SupportTicketPriorityEnum,
    SupportTicketStatusEnum,
    TransactionDebitStepStatusEnum,
    TransactionStatusEnum,
    TransactionTypeEnum,
)
from shared.money import require_naira
from shared.observability.events import emit_operational_event
from shared.utils.logging import get_logger

logger = get_logger(__name__)

ZERO = Decimal("0.00")


def funding_entry_key(funding_step_id: str) -> str:
    return f"funding_step:{funding_step_id}:mono_debit_confirmed"


def payout_entry_key(funded_transfer_id: str) -> str:
    return f"funded_transfer:{funded_transfer_id}:flutterwave_payout_confirmed"


def refund_entry_key(funding_step_id: str) -> str:
    return f"funding_step:{funding_step_id}:mono_refund_confirmed"


def transaction_debit_entry_key(transaction_debit_step_id: str) -> str:
    return f"transaction_debit_step:{transaction_debit_step_id}:mono_debit_confirmed"


def transaction_bill_entry_key(transaction_id: str) -> str:
    return f"transaction:{transaction_id}:flutterwave_bill_confirmed"


def transaction_debit_refund_entry_key(transaction_debit_step_id: str) -> str:
    return f"transaction_debit_step:{transaction_debit_step_id}:mono_refund_confirmed"


class LedgerPostingReconciliationConsumer:
    """Backfills missing ledger postings from terminal pooled-transfer workflow rows."""

    async def process_job(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.ledger_reconciliation_batch_size)
        scanned = 0
        repaired = 0
        findings = 0

        async with UnitOfWork() as uow:
            if not uow.ledger_reconciliation:
                return
            run = await uow.ledger_reconciliation.create_run(
                run_type="posting",
                details={"limit": limit},
            )
            try:
                funding_steps = await uow.ledger_reconciliation.list_confirmed_funding_steps(limit=limit)
                refunded_steps = await uow.ledger_reconciliation.list_refunded_funding_steps(limit=limit)
                completed_transfers = await uow.ledger_reconciliation.list_completed_funded_transfers(limit=limit)
                successful_transactions = await uow.ledger_reconciliation.list_successful_non_pooled_transactions(
                    limit=limit
                )
                transaction_debit_steps = await uow.ledger_reconciliation.list_confirmed_transaction_debit_steps(
                    limit=limit
                )
                debit_backed_bill_transactions = (
                    await uow.ledger_reconciliation.list_successful_debit_backed_bill_transactions(limit=limit)
                )
                refunded_transaction_debit_steps = (
                    await uow.ledger_reconciliation.list_refunded_transaction_debit_steps(limit=limit)
                )

                for step in funding_steps:
                    scanned += 1
                    repaired += await self._post_funding_if_missing(uow, step)
                for transfer in completed_transfers:
                    scanned += 1
                    repaired += await self._post_payout_if_missing(uow, transfer)
                for step in refunded_steps:
                    scanned += 1
                    repaired += await self._post_refund_if_missing(uow, step)
                for transaction in successful_transactions:
                    scanned += 1
                    repaired += await self._post_transaction_if_missing(uow, transaction)
                for step in transaction_debit_steps:
                    scanned += 1
                    repaired += await self._post_transaction_debit_if_missing(uow, step)
                for transaction in debit_backed_bill_transactions:
                    scanned += 1
                    repaired += await self._post_transaction_bill_if_missing(uow, transaction)
                for step in refunded_transaction_debit_steps:
                    scanned += 1
                    repaired += await self._post_transaction_debit_refund_if_missing(uow, step)

                await uow.ledger_reconciliation.finish_run(
                    run,
                    status="completed",
                    scanned_count=scanned,
                    repaired_count=repaired,
                    finding_count=findings,
                )
                await uow.commit()
            except Exception as exc:
                await uow.ledger_reconciliation.finish_run(
                    run,
                    status="failed",
                    scanned_count=scanned,
                    repaired_count=repaired,
                    finding_count=findings,
                    error_message=str(exc),
                )
                await uow.commit()
                raise

        logger.info("ledger_posting_reconciliation_completed", scanned=scanned, repaired=repaired)

    async def _post_funding_if_missing(self, uow: UnitOfWork, step: Any) -> int:
        if not uow.ledger_entries or not uow.funded_transfers:
            return 0
        key = funding_entry_key(str(step.id))
        existing = await uow.ledger_entries.get_by_key(key)
        transfer = await uow.funded_transfers.get_by_id(str(step.funded_transfer_id))
        if not transfer:
            return 0
        if existing:
            return 0
        try:
            await LedgerPostingService.post_mono_funding_confirmed(
                uow,
                step,
                transfer,
                provider_reference=getattr(step, "provider_reference", None)
                or getattr(step, "provider_debit_id", None),
            )
            return 1
        except LedgerEntryConflict as exc:
            await self._record_conflict(uow, transfer, step, key, FUNDING_ENTRY_TYPE, exc)
            return 0

    async def _post_payout_if_missing(self, uow: UnitOfWork, transfer: Any) -> int:
        if not uow.ledger_entries:
            return 0
        key = payout_entry_key(str(transfer.id))
        existing = await uow.ledger_entries.get_by_key(key)
        if existing:
            return 0
        try:
            await LedgerPostingService.post_flutterwave_payout_confirmed(
                uow,
                transfer,
                provider_reference=getattr(transfer, "payout_reference", None)
                or getattr(transfer, "idempotency_key", None),
            )
            return 1
        except LedgerEntryConflict as exc:
            await self._record_conflict(uow, transfer, None, key, PAYOUT_ENTRY_TYPE, exc)
            return 0

    async def _post_refund_if_missing(self, uow: UnitOfWork, step: Any) -> int:
        if not uow.ledger_entries or not uow.funded_transfers:
            return 0
        key = refund_entry_key(str(step.id))
        existing = await uow.ledger_entries.get_by_key(key)
        transfer = await uow.funded_transfers.get_by_id(str(step.funded_transfer_id))
        if not transfer:
            return 0
        if existing:
            return 0
        try:
            await LedgerPostingService.post_mono_refund_confirmed(
                uow,
                step,
                transfer,
                provider_reference=getattr(step, "refund_provider_reference", None)
                or getattr(step, "refund_provider_id", None)
                or getattr(step, "provider_reference", None),
            )
            return 1
        except LedgerEntryConflict as exc:
            await self._record_conflict(uow, transfer, step, key, REFUND_ENTRY_TYPE, exc)
            return 0

    async def _post_transaction_if_missing(self, uow: UnitOfWork, transaction: Any) -> int:
        if not uow.ledger_entries:
            return 0
        key = transaction_success_entry_key(transaction.id, transaction.transaction_type)
        existing = await uow.ledger_entries.get_by_key(key)
        if existing:
            return 0
        try:
            await LedgerPostingService.post_transaction_success_confirmed(
                uow,
                transaction,
                provider_reference=getattr(transaction, "transaction_id", None)
                or getattr(transaction, "idempotency_key", None),
            )
            return 1
        except LedgerEntryConflict as exc:
            await self._record_transaction_conflict(uow, transaction, key, exc)
            return 0

    async def _post_transaction_debit_if_missing(self, uow: UnitOfWork, step: Any) -> int:
        if not uow.ledger_entries or not uow.transactions:
            return 0
        key = transaction_debit_entry_key(str(step.id))
        if await uow.ledger_entries.get_by_key(key):
            return 0
        transaction = await uow.transactions.get_by_id(str(step.transaction_id))
        if not transaction:
            return 0
        try:
            await LedgerPostingService.post_transaction_debit_confirmed(
                uow,
                step,
                transaction,
                provider_reference=getattr(step, "provider_reference", None)
                or getattr(step, "provider_debit_id", None),
            )
            return 1
        except LedgerEntryConflict as exc:
            await self._record_transaction_conflict(uow, transaction, key, exc)
            return 0

    async def _post_transaction_bill_if_missing(self, uow: UnitOfWork, transaction: Any) -> int:
        if not uow.ledger_entries:
            return 0
        key = transaction_bill_entry_key(str(transaction.id))
        if await uow.ledger_entries.get_by_key(key):
            return 0
        try:
            await LedgerPostingService.post_transaction_bill_confirmed(
                uow,
                transaction,
                provider_reference=getattr(transaction, "transaction_id", None)
                or getattr(transaction, "idempotency_key", None),
                result=getattr(transaction, "provider_response", None),
            )
            return 1
        except LedgerEntryConflict as exc:
            await self._record_transaction_conflict(uow, transaction, key, exc)
            return 0

    async def _post_transaction_debit_refund_if_missing(self, uow: UnitOfWork, step: Any) -> int:
        if not uow.ledger_entries or not uow.transactions:
            return 0
        key = transaction_debit_refund_entry_key(str(step.id))
        if await uow.ledger_entries.get_by_key(key):
            return 0
        transaction = await uow.transactions.get_by_id(str(step.transaction_id))
        if not transaction:
            return 0
        try:
            await LedgerPostingService.post_transaction_debit_refund_confirmed(
                uow,
                step,
                transaction,
                provider_reference=getattr(step, "refund_provider_reference", None)
                or getattr(step, "refund_provider_id", None)
                or getattr(step, "provider_reference", None),
            )
            return 1
        except LedgerEntryConflict as exc:
            await self._record_transaction_conflict(uow, transaction, key, exc)
            return 0

    @staticmethod
    async def _record_conflict(
        uow: UnitOfWork,
        transfer: Any,
        step: Any | None,
        entry_key: str,
        entry_type: str,
        exc: LedgerEntryConflict,
    ) -> None:
        if not uow.ledger_reconciliation:
            return
        await uow.ledger_reconciliation.open_or_refresh_finding(
            finding_key=f"ledger_posting_conflict:{entry_key}",
            severity="critical",
            finding_type="posting_conflict",
            transaction_id=await LedgerPostingService._transaction_id_for_transfer(uow, transfer),
            funded_transfer_id=transfer.id,
            funding_step_id=getattr(step, "id", None),
            expected_amount_naira=require_naira(getattr(step or transfer, "amount", None)),
            actual_amount_naira=None,
            details={"entry_key": entry_key, "entry_type": entry_type, "error": str(exc)},
        )

    @staticmethod
    async def _record_transaction_conflict(
        uow: UnitOfWork,
        transaction: Any,
        entry_key: str,
        exc: LedgerEntryConflict,
    ) -> None:
        if not uow.ledger_reconciliation:
            return
        await uow.ledger_reconciliation.open_or_refresh_finding(
            finding_key=f"ledger_posting_conflict:{entry_key}",
            severity="critical",
            finding_type="transaction_posting_conflict",
            transaction_id=getattr(transaction, "id", None),
            funded_transfer_id=None,
            funding_step_id=None,
            expected_amount_naira=require_naira(getattr(transaction, "amount", None)),
            actual_amount_naira=None,
            details={
                "entry_key": entry_key,
                "transaction_type": getattr(transaction, "transaction_type", None),
                "error": str(exc),
            },
        )


class LedgerExposureReconciliationConsumer:
    """Finds unresolved pooled-transfer ledger exposure and workflow/accounting mismatches."""

    async def process_job(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.ledger_reconciliation_batch_size)
        min_age = int(payload.get("min_age_seconds") or settings.ledger_exposure_min_age_seconds)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=min_age)
        scanned = 0
        finding_count = 0

        async with UnitOfWork() as uow:
            if not uow.ledger_reconciliation:
                return
            run = await uow.ledger_reconciliation.create_run(
                run_type="exposure",
                details={"limit": limit, "min_age_seconds": min_age},
            )
            try:
                transfers = await uow.ledger_reconciliation.list_transfers_for_exposure_scan(
                    cutoff=cutoff,
                    limit=limit,
                )
                transaction_debit_steps = await uow.ledger_reconciliation.list_transaction_debits_for_exposure_scan(
                    cutoff=cutoff,
                    limit=limit,
                )
                direct_transfer_transactions = (
                    await uow.ledger_reconciliation.list_direct_transfer_transactions_for_exposure_scan(
                        cutoff=cutoff,
                        limit=limit,
                    )
                )
                for transfer in transfers:
                    scanned += 1
                    finding_count += await self._scan_transfer(uow, transfer)
                for step in transaction_debit_steps:
                    scanned += 1
                    finding_count += await self._scan_transaction_debit(uow, step)
                for transaction in direct_transfer_transactions:
                    scanned += 1
                    finding_count += await self._scan_direct_transfer_transaction(uow, transaction)
                await uow.ledger_reconciliation.finish_run(
                    run,
                    status="completed",
                    scanned_count=scanned,
                    repaired_count=0,
                    finding_count=finding_count,
                )
                await uow.commit()
            except Exception as exc:
                await uow.ledger_reconciliation.finish_run(
                    run,
                    status="failed",
                    scanned_count=scanned,
                    finding_count=finding_count,
                    error_message=str(exc),
                )
                await uow.commit()
                raise

        logger.info("ledger_exposure_reconciliation_completed", scanned=scanned, findings=finding_count)

    async def _scan_transfer(self, uow: UnitOfWork, transfer: Any) -> int:
        finding_count = 0
        finding_count += await self._scan_required_entries(uow, transfer)
        balance = await self._liability_balance(uow, transfer)
        finding_key = f"ledger_exposure:{transfer.id}"
        status = str(getattr(transfer, "status", "") or "")

        severity: str | None = None
        finding_type: str | None = None
        if status in {FundedTransferStatusEnum.COMPLETED.value, FundedTransferStatusEnum.REFUNDED.value}:
            if balance != ZERO:
                severity = "critical"
                finding_type = "terminal_non_zero_liability"
        elif status == FundedTransferStatusEnum.FAILED.value:
            if balance > ZERO:
                severity = "critical"
                finding_type = "failed_transfer_user_money_exposure"
        elif status == FundedTransferStatusEnum.REFUNDING.value and balance > ZERO:
            age_seconds = self._age_seconds(transfer)
            if age_seconds >= int(settings.ledger_stuck_refunding_seconds):
                severity = "high"
                finding_type = "stuck_refunding_user_money_exposure"

        if severity and finding_type:
            finding = await self._open_finding(
                uow,
                transfer,
                finding_key=finding_key,
                severity=severity,
                finding_type=finding_type,
                expected_amount_naira=ZERO,
                actual_amount_naira=balance,
                details={
                    "funded_transfer_id": str(transfer.id),
                    "transfer_status": status,
                    "liability_exposure_naira": str(balance),
                },
            )
            await self._ensure_support_ticket(uow, transfer, finding)
            return finding_count + 1

        if uow.ledger_reconciliation:
            await uow.ledger_reconciliation.resolve_finding(finding_key)
        return finding_count

    async def _scan_transaction_debit(self, uow: UnitOfWork, step: Any) -> int:
        if not uow.transactions:
            return 0
        transaction = await uow.transactions.get_by_id(str(step.transaction_id))
        if not transaction:
            return 0

        finding_count = 0
        finding_count += await self._scan_required_transaction_debit_entries(uow, step, transaction)
        balance = await self._transaction_liability_balance(uow, transaction)
        finding_key = f"ledger_exposure:transaction:{transaction.id}"
        transaction_status = str(getattr(transaction, "status", "") or "")
        step_status = str(getattr(step, "status", "") or "")

        severity: str | None = None
        finding_type: str | None = None
        if transaction_status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.REVERSED.value}:
            if balance != ZERO:
                severity = "critical"
                finding_type = "terminal_transaction_non_zero_liability"
        elif transaction_status == TransactionStatusEnum.FAILED.value and balance > ZERO:
            if step_status == TransactionDebitStepStatusEnum.REFUND_FAILED.value:
                severity = "critical"
                finding_type = "failed_transaction_user_money_exposure"
            elif step_status in {
                TransactionDebitStepStatusEnum.REFUND_PENDING.value,
                TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
            }:
                age_seconds = self._age_seconds(step)
                if age_seconds >= int(settings.ledger_stuck_refunding_seconds):
                    severity = "high"
                    finding_type = "stuck_transaction_refund_user_money_exposure"
            elif step_status == TransactionDebitStepStatusEnum.CONFIRMED.value:
                severity = "critical"
                finding_type = "failed_transaction_unqueued_refund_exposure"

        if severity and finding_type:
            finding = await self._open_transaction_finding(
                uow,
                transaction,
                finding_key=finding_key,
                severity=severity,
                finding_type=finding_type,
                expected_amount_naira=ZERO,
                actual_amount_naira=balance,
                details={
                    "transaction_id": str(transaction.id),
                    "transaction_status": transaction_status,
                    "transaction_debit_step_id": str(step.id),
                    "transaction_debit_status": step_status,
                    "liability_exposure_naira": str(balance),
                },
            )
            await self._ensure_transaction_support_ticket(uow, transaction, finding)
            return finding_count + 1

        if uow.ledger_reconciliation:
            await uow.ledger_reconciliation.resolve_finding(finding_key)
        return finding_count

    async def _scan_direct_transfer_transaction(self, uow: UnitOfWork, transaction: Any) -> int:
        return await self._check_required_transaction_entry(
            uow,
            transaction,
            key=transaction_success_entry_key(transaction.id, TransactionTypeEnum.TRANSFER.value),
            finding_type="missing_direct_transfer_ledger_entry",
            entry_type=DIRECT_TRANSFER_ENTRY_TYPE,
            severity="critical",
            expected_amount_naira=require_naira(getattr(transaction, "amount", None)),
        )

    async def _scan_required_transaction_debit_entries(
        self,
        uow: UnitOfWork,
        step: Any,
        transaction: Any,
    ) -> int:
        step_status = str(getattr(step, "status", "") or "")
        transaction_status = str(getattr(transaction, "status", "") or "")
        count = 0
        if step_status in {
            TransactionDebitStepStatusEnum.CONFIRMED.value,
            TransactionDebitStepStatusEnum.REFUND_PENDING.value,
            TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
            TransactionDebitStepStatusEnum.REFUND_FAILED.value,
            TransactionDebitStepStatusEnum.REFUNDED.value,
        }:
            count += await self._check_required_transaction_entry(
                uow,
                transaction,
                key=transaction_debit_entry_key(str(step.id)),
                finding_type="missing_transaction_debit_ledger_entry",
                entry_type=TRANSACTION_DEBIT_ENTRY_TYPE,
                severity="critical"
                if transaction_status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.REVERSED.value}
                else "high",
                expected_amount_naira=require_naira(getattr(step, "amount", None)),
            )
        if transaction_status == TransactionStatusEnum.SUCCESSFUL.value:
            count += await self._check_required_transaction_entry(
                uow,
                transaction,
                key=transaction_bill_entry_key(str(transaction.id)),
                finding_type="missing_transaction_bill_ledger_entry",
                entry_type=TRANSACTION_BILL_ENTRY_TYPE,
                severity="critical",
                expected_amount_naira=require_naira(getattr(transaction, "amount", None)),
            )
        if step_status == TransactionDebitStepStatusEnum.REFUNDED.value:
            count += await self._check_required_transaction_entry(
                uow,
                transaction,
                key=transaction_debit_refund_entry_key(str(step.id)),
                finding_type="missing_transaction_debit_refund_ledger_entry",
                entry_type=TRANSACTION_DEBIT_REFUND_ENTRY_TYPE,
                severity="critical" if transaction_status == TransactionStatusEnum.REVERSED.value else "high",
                expected_amount_naira=require_naira(getattr(step, "amount", None)),
            )
        return count

    async def _scan_required_entries(self, uow: UnitOfWork, transfer: Any) -> int:
        if not uow.funding_steps or not uow.ledger_entries:
            return 0
        count = 0
        steps = await uow.funding_steps.get_by_transfer(str(transfer.id))
        for step in steps:
            step_status = str(getattr(step, "status", "") or "")
            if step_status in {
                FundingStepStatusEnum.CONFIRMED.value,
                FundingStepStatusEnum.REFUND_PENDING.value,
                FundingStepStatusEnum.REFUND_PROCESSING.value,
                FundingStepStatusEnum.REFUND_FAILED.value,
                FundingStepStatusEnum.REFUNDED.value,
            }:
                count += await self._check_required_entry(
                    uow,
                    transfer,
                    step,
                    key=funding_entry_key(str(step.id)),
                    finding_type="missing_funding_ledger_entry",
                    entry_type=FUNDING_ENTRY_TYPE,
                    severity="critical" if self._is_terminal_transfer(transfer) else "high",
                )
            if step_status == FundingStepStatusEnum.REFUNDED.value:
                count += await self._check_required_entry(
                    uow,
                    transfer,
                    step,
                    key=refund_entry_key(str(step.id)),
                    finding_type="missing_refund_ledger_entry",
                    entry_type=REFUND_ENTRY_TYPE,
                    severity="critical" if self._is_terminal_transfer(transfer) else "high",
                )

        if getattr(transfer, "status", None) == FundedTransferStatusEnum.COMPLETED.value:
            count += await self._check_required_entry(
                uow,
                transfer,
                None,
                key=payout_entry_key(str(transfer.id)),
                finding_type="missing_payout_ledger_entry",
                entry_type=PAYOUT_ENTRY_TYPE,
                severity="critical",
            )
        return count

    async def _check_required_entry(
        self,
        uow: UnitOfWork,
        transfer: Any,
        step: Any | None,
        *,
        key: str,
        finding_type: str,
        entry_type: str,
        severity: str,
    ) -> int:
        assert uow.ledger_entries is not None
        entry = await uow.ledger_entries.get_by_key(key)
        finding_key = f"ledger_missing_entry:{key}"
        if entry:
            if uow.ledger_reconciliation:
                await uow.ledger_reconciliation.resolve_finding(finding_key)
            return 0

        finding = await self._open_finding(
            uow,
            transfer,
            finding_key=finding_key,
            severity=severity,
            finding_type=finding_type,
            expected_amount_naira=require_naira(getattr(step or transfer, "amount", None)),
            actual_amount_naira=None,
            details={
                "entry_key": key,
                "entry_type": entry_type,
                "funded_transfer_id": str(transfer.id),
                "funding_step_id": str(getattr(step, "id", "")) if step else None,
            },
        )
        if severity == "critical":
            await self._ensure_support_ticket(uow, transfer, finding)
        return 1

    async def _check_required_transaction_entry(
        self,
        uow: UnitOfWork,
        transaction: Any,
        *,
        key: str,
        finding_type: str,
        entry_type: str,
        severity: str,
        expected_amount_naira: Decimal,
    ) -> int:
        if not uow.ledger_entries:
            return 0
        entry = await uow.ledger_entries.get_by_key(key)
        finding_key = f"ledger_missing_entry:{key}"
        if entry:
            if uow.ledger_reconciliation:
                await uow.ledger_reconciliation.resolve_finding(finding_key)
            return 0

        finding = await self._open_transaction_finding(
            uow,
            transaction,
            finding_key=finding_key,
            severity=severity,
            finding_type=finding_type,
            expected_amount_naira=expected_amount_naira,
            actual_amount_naira=None,
            details={
                "entry_key": key,
                "entry_type": entry_type,
                "transaction_id": str(transaction.id),
                "transaction_type": getattr(transaction, "transaction_type", None),
            },
        )
        if severity == "critical":
            await self._ensure_transaction_support_ticket(uow, transaction, finding)
        return 1

    async def _liability_balance(self, uow: UnitOfWork, transfer: Any) -> Decimal:
        if not uow.ledger_accounts or not uow.ledger_entries:
            return ZERO
        account = await uow.ledger_accounts.get_by_code(f"liability:funded_transfer:{transfer.id}:NGN")
        if not account:
            return ZERO
        return await uow.ledger_entries.liability_balance_for_transfer(liability_account_id=account.id)

    async def _transaction_liability_balance(self, uow: UnitOfWork, transaction: Any) -> Decimal:
        if not uow.ledger_accounts or not uow.ledger_entries:
            return ZERO
        account = await uow.ledger_accounts.get_by_code(f"liability:transaction:{transaction.id}:customer_funds:NGN")
        if not account:
            return ZERO
        return await uow.ledger_entries.liability_balance_for_transaction(liability_account_id=account.id)

    async def _open_finding(
        self,
        uow: UnitOfWork,
        transfer: Any,
        *,
        finding_key: str,
        severity: str,
        finding_type: str,
        expected_amount_naira: Decimal | None,
        actual_amount_naira: Decimal | None,
        details: dict[str, Any],
    ) -> Any:
        if not uow.ledger_reconciliation:
            return None
        return await uow.ledger_reconciliation.open_or_refresh_finding(
            finding_key=finding_key,
            severity=severity,
            finding_type=finding_type,
            transaction_id=await LedgerPostingService._transaction_id_for_transfer(uow, transfer),
            funded_transfer_id=transfer.id,
            expected_amount_naira=expected_amount_naira,
            actual_amount_naira=actual_amount_naira,
            details=details,
        )

    async def _open_transaction_finding(
        self,
        uow: UnitOfWork,
        transaction: Any,
        *,
        finding_key: str,
        severity: str,
        finding_type: str,
        expected_amount_naira: Decimal | None,
        actual_amount_naira: Decimal | None,
        details: dict[str, Any],
    ) -> Any:
        if not uow.ledger_reconciliation:
            return None
        return await uow.ledger_reconciliation.open_or_refresh_finding(
            finding_key=finding_key,
            severity=severity,
            finding_type=finding_type,
            transaction_id=getattr(transaction, "id", None),
            expected_amount_naira=expected_amount_naira,
            actual_amount_naira=actual_amount_naira,
            details=details,
        )

    async def _ensure_support_ticket(self, uow: UnitOfWork, transfer: Any, finding: Any) -> None:
        if not settings.ledger_findings_create_support_ticket:
            return
        tickets = getattr(uow, "support_tickets", None)
        if not tickets or not finding or getattr(finding, "support_ticket_id", None):
            return
        existing = await tickets.get_by_transaction_ref(str(transfer.idempotency_key))
        for ticket in existing:
            if getattr(ticket, "intent", None) == "ledger_reconciliation":
                finding.support_ticket_id = ticket.id
                if uow.db:
                    uow.db.add(finding)
                emit_operational_event(
                    "ledger_reconciliation_support_ticket_reused",
                    severity="info",
                    domain="ledger",
                    identifiers={
                        "support_ticket_id": getattr(ticket, "id", None),
                        "funded_transfer_id": getattr(transfer, "id", None),
                        "finding_key": getattr(finding, "finding_key", None),
                    },
                    details={"finding_type": getattr(finding, "finding_type", None)},
                )
                return

        ticket_code = await tickets.generate_ticket_code()
        ticket = await tickets.create(
            ticket_code=ticket_code,
            user_id=str(transfer.user_id),
            channel="system",
            intent="ledger_reconciliation",
            status=SupportTicketStatusEnum.OPEN.value,
            priority=SupportTicketPriorityEnum.URGENT.value
            if getattr(finding, "severity", "") == "critical"
            else SupportTicketPriorityEnum.HIGH.value,
            transaction_ref=str(transfer.idempotency_key),
            summary="Ledger reconciliation finding requires review",
            details={
                "finding_key": finding.finding_key,
                "finding_type": finding.finding_type,
                "funded_transfer_id": str(transfer.id),
                "severity": finding.severity,
            },
        )
        finding.support_ticket_id = ticket.id
        if uow.db:
            uow.db.add(finding)
        emit_operational_event(
            "ledger_reconciliation_support_ticket_created",
            severity="critical" if getattr(finding, "severity", "") == "critical" else "high",
            domain="ledger",
            identifiers={
                "support_ticket_id": getattr(ticket, "id", None),
                "funded_transfer_id": getattr(transfer, "id", None),
                "finding_key": getattr(finding, "finding_key", None),
            },
            details={"finding_type": getattr(finding, "finding_type", None)},
        )

    async def _ensure_transaction_support_ticket(self, uow: UnitOfWork, transaction: Any, finding: Any) -> None:
        if not settings.ledger_findings_create_support_ticket:
            return
        tickets = getattr(uow, "support_tickets", None)
        if not tickets or not finding or getattr(finding, "support_ticket_id", None):
            return
        transaction_ref = str(getattr(transaction, "idempotency_key", None) or getattr(transaction, "id", ""))
        existing = await tickets.get_by_transaction_ref(transaction_ref)
        for ticket in existing:
            if getattr(ticket, "intent", None) == "ledger_reconciliation":
                finding.support_ticket_id = ticket.id
                if uow.db:
                    uow.db.add(finding)
                emit_operational_event(
                    "ledger_reconciliation_support_ticket_reused",
                    severity="info",
                    domain="ledger",
                    identifiers={
                        "support_ticket_id": getattr(ticket, "id", None),
                        "transaction_id": getattr(transaction, "id", None),
                        "finding_key": getattr(finding, "finding_key", None),
                    },
                    details={"finding_type": getattr(finding, "finding_type", None)},
                )
                return

        ticket_code = await tickets.generate_ticket_code()
        ticket = await tickets.create(
            ticket_code=ticket_code,
            user_id=str(getattr(transaction, "user_id", "")),
            channel="system",
            intent="ledger_reconciliation",
            status=SupportTicketStatusEnum.OPEN.value,
            priority=SupportTicketPriorityEnum.URGENT.value
            if getattr(finding, "severity", "") == "critical"
            else SupportTicketPriorityEnum.HIGH.value,
            transaction_ref=transaction_ref,
            summary="Ledger reconciliation finding requires review",
            details={
                "finding_key": finding.finding_key,
                "finding_type": finding.finding_type,
                "transaction_id": str(getattr(transaction, "id", "")),
                "severity": finding.severity,
            },
        )
        finding.support_ticket_id = ticket.id
        if uow.db:
            uow.db.add(finding)
        emit_operational_event(
            "ledger_reconciliation_support_ticket_created",
            severity="critical" if getattr(finding, "severity", "") == "critical" else "high",
            domain="ledger",
            identifiers={
                "support_ticket_id": getattr(ticket, "id", None),
                "transaction_id": getattr(transaction, "id", None),
                "finding_key": getattr(finding, "finding_key", None),
            },
            details={"finding_type": getattr(finding, "finding_type", None)},
        )

    @staticmethod
    def _is_terminal_transfer(transfer: Any) -> bool:
        return str(getattr(transfer, "status", "") or "") in {
            FundedTransferStatusEnum.COMPLETED.value,
            FundedTransferStatusEnum.REFUNDED.value,
            FundedTransferStatusEnum.FAILED.value,
        }

    @staticmethod
    def _age_seconds(transfer: Any) -> int:
        now = datetime.now(UTC).replace(tzinfo=None)
        base = (
            getattr(transfer, "updated_at", None)
            or getattr(transfer, "refund_last_checked_at", None)
            or getattr(transfer, "refund_initiated_at", None)
            or getattr(transfer, "confirmed_at", None)
            or getattr(transfer, "created_at", None)
            or now
        )
        if getattr(base, "tzinfo", None) is not None:
            base = base.astimezone(UTC).replace(tzinfo=None)
        return max(int((now - base).total_seconds()), 0)
