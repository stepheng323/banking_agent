"""Consumers for Flutterwave airtime/data bill fulfillment after Mono debit."""

from datetime import UTC, datetime
from typing import Any

from banking.ledger.service import LedgerPostingService
from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime import provider_results
from banking.transactions.runtime.bill_completion_notifications import BillCompletionNotifier
from banking.transactions.runtime.transaction_debit_helpers import (
    bill_reference_for_transaction,
    queue_transaction_debit_refund,
)
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.config.settings import settings
from shared.database.enums import TransactionDebitStepStatusEnum, TransactionStatusEnum, TransactionTypeEnum
from shared.money import require_naira
from shared.queue.adapter import QueuePublisher
from shared.utils.json import to_json_safe_dict
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_BILL_CLAIMED_STATUSES = {"bill_claimed", "bill_processing", "pending"}
_BILL_SUCCESS_STATUSES = {"success", "successful", "completed", "paid"}
_BILL_FAILURE_STATUSES = {"failed", "failure", "cancelled", "canceled", "reversed"}
_BILL_PENDING_STATUSES = {"pending", "processing", "queued", "in_progress"}


def normalize_bill_status(result: dict[str, Any]) -> str:
    """Normalize bill provider response into successful, failed, or pending."""
    status = provider_results.provider_status(result)
    raw_status = str(status or result.get("status") or result.get("provider_status") or "").strip().lower()
    if raw_status in _BILL_SUCCESS_STATUSES:
        return "successful"
    if raw_status in _BILL_FAILURE_STATUSES:
        return "failed"
    if raw_status in _BILL_PENDING_STATUSES:
        return "pending"
    if result.get("success") is True:
        return "successful"
    if provider_results.provider_status_is_processing(result):
        return "pending"
    return "failed"


class BillFulfillmentConsumer:
    """Fulfills airtime/data bill payment after a confirmed transaction debit."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        publisher: QueuePublisher,
        notifier: BillCompletionNotifier | None = None,
    ):
        self.bill_provider = bill_provider
        self.publisher = publisher
        self.notifier = notifier

    async def process_job(self, payload: dict[str, Any]) -> None:
        transaction_id = str(payload.get("transaction_id") or "")
        if not transaction_id:
            logger.warning("bill_fulfillment_job_missing_transaction_id", payload=payload)
            return
        reconcile = bool(payload.get("reconcile"))
        claim = await self._claim(transaction_id, reconcile=reconcile)
        if claim is None:
            return

        if claim.get("status_lookup"):
            result = await self.bill_provider.get_bill_status(claim["bill_reference"])
        else:
            result = await self._purchase_bill(claim)
        outcome = await self._apply_result(transaction_id, claim["debit_step_id"], claim["bill_reference"], result)
        await self._notify_completion(transaction_id, outcome, result)

    async def _claim(self, transaction_id: str, *, reconcile: bool = False) -> dict[str, Any] | None:
        async with UnitOfWork() as uow:
            if not uow.transactions or not uow.transaction_debit_steps:
                return None
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            if not tx:
                return None
            if tx.status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.REVERSED.value}:
                return None
            step = await uow.transaction_debit_steps.get_by_transaction_for_update(str(tx.id))
            if not step or step.status != TransactionDebitStepStatusEnum.CONFIRMED.value:
                logger.info(
                    "bill_fulfillment_skipped_unconfirmed_debit",
                    transaction_id=str(tx.id),
                    debit_status=getattr(step, "status", None),
                )
                return None

            bill_reference = ""
            if tx.provider_status in _BILL_CLAIMED_STATUSES:
                bill_reference = str(getattr(tx, "transaction_id", None) or "")
            bill_reference = bill_reference or bill_reference_for_transaction(tx)
            if tx.provider_status in _BILL_CLAIMED_STATUSES and not reconcile:
                logger.info("bill_fulfillment_already_claimed", transaction_id=str(tx.id))
                return None

            status_lookup = reconcile and tx.provider_status in _BILL_CLAIMED_STATUSES and bool(tx.transaction_id)
            if not status_lookup:
                tx.provider_status = "bill_claimed"
                tx.transaction_id = bill_reference
                if uow.db:
                    uow.db.add(tx)
                await uow.commit()

            return {
                "transaction_id": str(tx.id),
                "transaction_type": str(tx.transaction_type),
                "amount": require_naira(tx.amount),
                "target_phone_number": tx.target_phone_number,
                "mobile_network": tx.mobile_network,
                "biller_item_code": tx.biller_item_code,
                "bill_reference": bill_reference,
                "debit_step_id": str(step.id),
                "status_lookup": status_lookup,
            }
        return None

    async def _purchase_bill(self, claim: dict[str, Any]) -> dict[str, Any]:
        transaction_type = claim["transaction_type"]
        if transaction_type == TransactionTypeEnum.AIRTIME.value:
            return await self.bill_provider.purchase_airtime(
                amount=claim["amount"],
                recipient_phone=str(claim["target_phone_number"] or ""),
                network=str(claim["mobile_network"] or ""),
                reference=claim["bill_reference"],
            )
        if transaction_type == TransactionTypeEnum.DATA.value:
            return await self.bill_provider.purchase_data(
                plan_code=str(claim["biller_item_code"] or ""),
                recipient_phone=str(claim["target_phone_number"] or ""),
                network=str(claim["mobile_network"] or ""),
                amount=claim["amount"],
                reference=claim["bill_reference"],
            )
        return {"success": False, "error": f"Unsupported bill transaction type: {transaction_type}"}

    async def _apply_result(
        self,
        transaction_id: str,
        debit_step_id: str,
        bill_reference: str,
        result: dict[str, Any],
    ) -> str:
        async with UnitOfWork() as uow:
            if not uow.transactions or not uow.transaction_debit_steps:
                return "skipped"
            tx = await uow.transactions.get_by_id_for_update(transaction_id)
            step = await uow.transaction_debit_steps.get_by_id_for_update(debit_step_id)
            if not tx or not step:
                return "skipped"
            if tx.status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.REVERSED.value}:
                return "skipped"
            if step.status != TransactionDebitStepStatusEnum.CONFIRMED.value:
                return "skipped"

            safe_result = to_json_safe_dict(result)
            provider_reference = provider_results.provider_reference(result) or bill_reference
            status = normalize_bill_status(result)
            tx.transaction_id = provider_reference
            tx.provider_status = provider_results.provider_status(result) or status
            tx.provider_response = safe_result

            if status == "successful":
                await LedgerPostingService.post_transaction_bill_confirmed(
                    uow,
                    tx,
                    provider_reference=provider_reference,
                    result=safe_result,
                )
                tx.status = TransactionStatusEnum.SUCCESSFUL.value
                tx.completed_at = datetime.now(UTC).replace(tzinfo=None)
                if uow.db:
                    uow.db.add(tx)
                await uow.commit()
                logger.info("bill_fulfillment_successful", transaction_id=str(tx.id))
                return "successful"

            if status == "pending":
                tx.status = TransactionStatusEnum.PROCESSING.value
                tx.provider_status = "bill_processing"
                if uow.db:
                    uow.db.add(tx)
                await uow.commit()
                logger.info("bill_fulfillment_pending", transaction_id=str(tx.id))
                return "pending"

            error = provider_results.provider_error_message(result, "Bill payment failed")
            tx.status = TransactionStatusEnum.FAILED.value
            tx.error_message = error
            tx.completed_at = datetime.now(UTC).replace(tzinfo=None)
            await uow.transaction_debit_steps.update_status(
                str(step.id),
                TransactionDebitStepStatusEnum.REFUND_PENDING.value,
                error_message=error,
            )
            if uow.db:
                uow.db.add(tx)
            await queue_transaction_debit_refund(publisher=self.publisher, debit_step=step, transaction=tx)
            await uow.commit()
            logger.warning("bill_fulfillment_failed_refund_queued", transaction_id=str(tx.id), error=error)
            return "failed"
        return "skipped"

    async def _notify_completion(self, transaction_id: str, outcome: str, result: dict[str, Any]) -> None:
        if self.notifier is None:
            return
        error_message = provider_results.provider_error_message(result, "Bill payment failed")
        if outcome == "successful":
            await self.notifier.notify_by_transaction_id(transaction_id, "successful")
        elif outcome == "pending":
            await self.notifier.notify_by_transaction_id(transaction_id, "processing")
        elif outcome == "failed":
            await self.notifier.notify_by_transaction_id(
                transaction_id,
                "failed_refund_pending",
                error_message=error_message,
            )


class BillReconciliationConsumer:
    """Recovers stale bill fulfillment after confirmed transaction debits."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        publisher: QueuePublisher,
        notifier: BillCompletionNotifier | None = None,
    ):
        self.bill_provider = bill_provider
        self.publisher = publisher
        self.notifier = notifier

    async def process_job(self, payload: dict[str, Any]) -> None:
        transaction_id = payload.get("transaction_id")
        if transaction_id:
            await BillFulfillmentConsumer(self.bill_provider, self.publisher, notifier=self.notifier).process_job(
                {"transaction_id": str(transaction_id), "reconcile": True}
            )
            return
        await self._reconcile_batch(payload)

    async def _reconcile_batch(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.bill_reconciliation_batch_size)
        async with UnitOfWork() as uow:
            if not uow.transaction_debit_steps:
                return
            steps = await uow.transaction_debit_steps.get_confirmed_without_success(limit=limit)
            transaction_ids = [str(step.transaction_id) for step in steps]

        for transaction_id in transaction_ids:
            await BillFulfillmentConsumer(self.bill_provider, self.publisher, notifier=self.notifier).process_job(
                {"transaction_id": transaction_id, "reconcile": True}
            )
