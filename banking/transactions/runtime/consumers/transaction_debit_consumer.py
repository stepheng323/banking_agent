"""Consumer for Mono account debits before airtime/data bill fulfillment."""

from datetime import UTC, datetime, timedelta
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.bill_completion_notifications import BillCompletionEvent, BillCompletionNotifier
from banking.transactions.runtime.transaction_debit_helpers import (
    apply_transaction_debit_result,
    debit_reference_for_transaction,
    queue_bill_fulfillment,
)
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus, DirectDebitProvider
from shared.config.settings import settings
from shared.database.enums import TransactionDebitStepStatusEnum, TransactionStatusEnum
from shared.money import require_naira
from shared.observability.events import emit_operational_event
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionDebitConsumer:
    """Starts Mono debit for a single airtime/data transaction."""

    def __init__(
        self,
        direct_debit_provider: DirectDebitProvider,
        publisher: QueuePublisher,
        notifier: BillCompletionNotifier | None = None,
    ):
        self.direct_debit_provider = direct_debit_provider
        self.publisher = publisher
        self.notifier = notifier

    @property
    def account_provider_name(self) -> str:
        return str(getattr(self.direct_debit_provider, "provider_name", settings.account_provider_name))

    async def process_job(self, payload: dict[str, Any]) -> None:
        transaction_id = str(payload.get("transaction_id") or "")
        if not transaction_id:
            logger.warning("transaction_debit_job_missing_transaction_id", payload=payload)
            return
        await self._process_transaction(transaction_id)

    async def _process_transaction(self, transaction_id: str) -> None:
        claim = await self._claim(transaction_id)
        if claim is None:
            return
        if claim.get("bill_ready"):
            await queue_bill_fulfillment(publisher=self.publisher, transaction=claim["transaction"])
            return
        if claim.get("failed"):
            await self._notify(transaction_id, "debit_failed")
            return

        result = await self.direct_debit_provider.initiate_pooling_debit(
            mandate_id=claim["mandate_id"],
            amount=claim["amount"],
            reference=claim["reference"],
            narration=claim["narration"],
        )
        outcome = await self._apply_result(transaction_id, str(claim["step_id"]), claim["reference"], result)
        if outcome == "failed":
            await self._notify(transaction_id, "debit_failed", error_message=result.error_message)

    async def _claim(self, transaction_id: str) -> dict[str, Any] | None:
        async with UnitOfWork() as uow:
            if not uow.transactions or not uow.transaction_debit_steps or not uow.accounts:
                return None
            get_tx = getattr(uow.transactions, "get_by_id_for_update", uow.transactions.get_by_id)
            transaction = await get_tx(transaction_id)
            if not transaction:
                logger.warning("transaction_debit_transaction_not_found", transaction_id=transaction_id)
                return None
            if transaction.status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.REVERSED.value}:
                return None

            step = await uow.transaction_debit_steps.get_by_transaction_for_update(str(transaction.id))
            if not step:
                account_id = getattr(transaction, "source_account_id", None)
                if not account_id:
                    await self._fail_transaction(uow, transaction, "Source account missing for debit")
                    await uow.commit()
                    return {"failed": True}
                step, _ = await uow.transaction_debit_steps.get_or_create_for_transaction(
                    transaction=transaction,
                    account_id=str(account_id),
                    provider_reference=debit_reference_for_transaction(transaction),
                    provider_name=self.account_provider_name,
                )

            if step.status == TransactionDebitStepStatusEnum.CONFIRMED.value:
                return {"bill_ready": True, "transaction": transaction}
            if step.status != TransactionDebitStepStatusEnum.PENDING.value:
                return None

            account = await uow.accounts.get_by_id(str(step.account_id))
            if not account or not account.mandate_id:
                await uow.transaction_debit_steps.update_status(
                    str(step.id),
                    TransactionDebitStepStatusEnum.FAILED.value,
                    error_message="Mandate not available for source account",
                )
                await self._fail_transaction(uow, transaction, "Mandate not available for source account")
                await uow.commit()
                return {"failed": True}

            reference = step.provider_reference or debit_reference_for_transaction(transaction)
            claimed = await uow.transaction_debit_steps.claim_for_debit(
                str(step.id),
                provider_reference=reference,
                provider_name=self.account_provider_name,
            )
            if not claimed:
                return None
            transaction.status = TransactionStatusEnum.PROCESSING.value
            transaction.provider_status = "debit_processing"
            if uow.db:
                uow.db.add(transaction)
            await uow.commit()
            return {
                "step_id": str(step.id),
                "mandate_id": str(account.mandate_id),
                "amount": require_naira(step.amount),
                "reference": reference,
                "narration": transaction.narration or "Bill payment funding",
            }
        return None

    async def _apply_result(
        self,
        transaction_id: str,
        step_id: str,
        reference: str,
        result: Any,
    ) -> str:
        async with UnitOfWork() as uow:
            if not uow.transactions or not uow.transaction_debit_steps:
                return "skipped"
            step = await uow.transaction_debit_steps.get_by_id_for_update(step_id)
            transaction = await uow.transactions.get_by_id_for_update(transaction_id)
            if not step or not transaction:
                return "skipped"
            outcome = await apply_transaction_debit_result(
                uow=uow,
                debit_step=step,
                transaction=transaction,
                result=result,
                reference=reference,
                publisher=self.publisher,
            )
            await uow.commit()
            return outcome
        return "skipped"

    async def _notify(
        self,
        transaction_id: str,
        event: BillCompletionEvent,
        *,
        error_message: str | None = None,
    ) -> None:
        if self.notifier is None:
            return
        await self.notifier.notify_by_transaction_id(
            transaction_id,
            event,
            error_message=error_message,
        )

    @staticmethod
    async def _fail_transaction(uow: UnitOfWork, transaction: Any, error_message: str) -> None:
        transaction.status = TransactionStatusEnum.FAILED.value
        transaction.error_message = error_message
        transaction.provider_status = "debit_failed"
        transaction.completed_at = datetime.now(UTC).replace(tzinfo=None)
        if uow.db:
            uow.db.add(transaction)


class TransactionDebitReconciliationConsumer:
    """Recovers stale transaction debit steps and missed bill fulfillment publishes."""

    def __init__(
        self,
        direct_debit_provider: DirectDebitProvider,
        publisher: QueuePublisher,
        notifier: BillCompletionNotifier | None = None,
    ):
        self.direct_debit_provider = direct_debit_provider
        self.publisher = publisher
        self.notifier = notifier

    @property
    def account_provider_name(self) -> str:
        return str(getattr(self.direct_debit_provider, "provider_name", settings.account_provider_name))

    async def process_job(self, payload: dict[str, Any]) -> None:
        transaction_id = payload.get("transaction_id")
        if transaction_id:
            consumer = TransactionDebitConsumer(
                self.direct_debit_provider,
                self.publisher,
                notifier=self.notifier,
            )
            await consumer._process_transaction(str(transaction_id))
            return
        await self._reconcile_batch(payload)

    async def _reconcile_batch(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.transaction_debit_reconciliation_batch_size)
        min_age = int(payload.get("min_age_seconds") or settings.transaction_debit_reconciliation_min_age_seconds)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=min_age)

        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps:
                return
            open_steps = await uow.transaction_debit_steps.get_recoverable_open(cutoff=cutoff, limit=limit)
            confirmed_steps = await uow.transaction_debit_steps.get_confirmed_without_success(limit=limit)
            targets = [(str(step.id), str(step.transaction_id), step.provider_debit_id) for step in open_steps]
            bill_targets = [str(step.transaction_id) for step in confirmed_steps]
        if targets:
            emit_operational_event(
                "transaction_debit_stuck_steps_found",
                severity="warning",
                domain="bill",
                details={"count": len(targets), "min_age_seconds": min_age},
            )
        if bill_targets:
            emit_operational_event(
                "transaction_debit_confirmed_bill_missing_found",
                severity="warning",
                domain="bill",
                details={"count": len(bill_targets)},
            )

        for step_id, transaction_id, provider_debit_id in targets:
            if provider_debit_id:
                result = await self.direct_debit_provider.get_debit_status(str(provider_debit_id))
                await self._apply_status_result(transaction_id, step_id, result)
            else:
                await self._retry_open_step(transaction_id, step_id)

        for transaction_id in bill_targets:
            await queue_bill_fulfillment_for_transaction_id(self.publisher, transaction_id)

    async def _apply_status_result(self, transaction_id: str, step_id: str, result: DebitResult) -> None:
        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps or not uow.transactions:
                return
            step = await uow.transaction_debit_steps.get_by_id_for_update(step_id)
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not step or not tx:
                return
            outcome = await apply_transaction_debit_result(
                uow=uow,
                debit_step=step,
                transaction=tx,
                result=result,
                reference=step.provider_reference or debit_reference_for_transaction(tx),
                publisher=self.publisher,
            )
            await uow.commit()
        emit_operational_event(
            "transaction_debit_reconciliation_applied",
            severity="high" if outcome == "failed" else "warning" if outcome == "processing" else "info",
            domain="bill",
            identifiers={"transaction_id": transaction_id, "transaction_debit_step_id": step_id},
            details={"outcome": outcome, "provider_status": getattr(result.status, "value", result.status)},
        )
        if outcome == "failed":
            await self._notify(transaction_id, "debit_failed", error_message=result.error_message)

    async def _retry_open_step(self, transaction_id: str, step_id: str) -> None:
        """Re-drive stale pending/processing debits using the deterministic Mono reference."""
        claim = await self._claim_open_step_for_retry(transaction_id, step_id)
        if claim is None:
            return
        result = await self.direct_debit_provider.initiate_pooling_debit(
            mandate_id=claim["mandate_id"],
            amount=claim["amount"],
            reference=claim["reference"],
            narration=claim["narration"],
        )
        await self._apply_status_result(transaction_id, step_id, result)

    async def _claim_open_step_for_retry(self, transaction_id: str, step_id: str) -> dict[str, Any] | None:
        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps or not uow.transactions or not uow.accounts:
                return None
            step = await uow.transaction_debit_steps.get_by_id_for_update(step_id)
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not step or not tx:
                return None
            if step.status not in {
                TransactionDebitStepStatusEnum.PENDING.value,
                TransactionDebitStepStatusEnum.PROCESSING.value,
            }:
                return None
            if step.provider_debit_id:
                return None

            retry_count = int(getattr(step, "retry_count", 0) or 0)
            reference = step.provider_reference or debit_reference_for_transaction(tx)
            if retry_count >= int(settings.funding_step_max_retries):
                result = DebitResult(
                    success=False,
                    status=DebitStatus.FAILED,
                    reference=reference,
                    error_message="Transaction debit retry limit exhausted",
                )
                outcome = await apply_transaction_debit_result(
                    uow=uow,
                    debit_step=step,
                    transaction=tx,
                    result=result,
                    reference=reference,
                    publisher=self.publisher,
                )
                await uow.commit()
                if outcome == "failed":
                    await self._notify(transaction_id, "debit_failed", error_message=result.error_message)
                return None

            account = await uow.accounts.get_by_id(str(step.account_id))
            if not account or not account.mandate_id:
                result = DebitResult(
                    success=False,
                    status=DebitStatus.FAILED,
                    reference=reference,
                    error_message="Mandate not available for source account",
                )
                outcome = await apply_transaction_debit_result(
                    uow=uow,
                    debit_step=step,
                    transaction=tx,
                    result=result,
                    reference=reference,
                    publisher=self.publisher,
                )
                await uow.commit()
                if outcome == "failed":
                    await self._notify(transaction_id, "debit_failed", error_message=result.error_message)
                return None

            if step.status == TransactionDebitStepStatusEnum.PENDING.value:
                claimed = await uow.transaction_debit_steps.claim_for_debit(
                    str(step.id),
                    provider_reference=reference,
                    provider_name=self.account_provider_name,
                )
                if not claimed:
                    return None
            else:
                await uow.transaction_debit_steps.update_status(
                    str(step.id),
                    TransactionDebitStepStatusEnum.PROCESSING.value,
                    provider_reference=reference,
                )

            tx.status = TransactionStatusEnum.PROCESSING.value
            tx.provider_status = "debit_processing"
            if uow.db:
                uow.db.add(tx)
            await uow.commit()
            return {
                "mandate_id": str(account.mandate_id),
                "amount": require_naira(step.amount),
                "reference": reference,
                "narration": tx.narration or "Bill payment funding",
            }
        return None

    async def _notify(
        self,
        transaction_id: str,
        event: BillCompletionEvent,
        *,
        error_message: str | None = None,
    ) -> None:
        if self.notifier is None:
            return
        await self.notifier.notify_by_transaction_id(
            transaction_id,
            event,
            error_message=error_message,
        )


async def queue_bill_fulfillment_for_transaction_id(publisher: QueuePublisher, transaction_id: str) -> None:
    """Queue bill fulfillment for a transaction ID after looking up its idempotency key."""
    async with UnitOfWork() as uow:
        if not uow.transactions:
            return
        tx = await uow.transactions.get_by_id(transaction_id)
        if not tx:
            return
        await queue_bill_fulfillment(publisher=publisher, transaction=tx)
