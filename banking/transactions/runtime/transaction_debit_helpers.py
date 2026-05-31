"""Helpers for single-transaction debit, bill, and refund flows."""

from datetime import UTC, datetime
from typing import Any

from banking.ledger.service import LedgerPostingService
from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.funding_status import is_retryable_debit_result
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.config.settings import settings
from shared.database.enums import TransactionDebitStepStatusEnum, TransactionStatusEnum
from shared.money import naira_to_json
from shared.queue.adapter import QueuePublisher
from shared.utils.json import to_json_safe_dict
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_DEBIT_OPEN_STATUSES = {
    TransactionDebitStepStatusEnum.PENDING.value,
    TransactionDebitStepStatusEnum.PROCESSING.value,
}

TRANSACTION_DEBIT_REFUND_IN_FLIGHT = {
    TransactionDebitStepStatusEnum.REFUND_PENDING.value,
    TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
}


def debit_reference_for_transaction(transaction: Any) -> str:
    """Return deterministic Mono debit reference for an airtime/data transaction."""
    return f"{transaction.idempotency_key}-debit"


def bill_reference_for_transaction(transaction: Any) -> str:
    """Return deterministic Flutterwave bill reference for an airtime/data transaction."""
    return f"{transaction.idempotency_key}-bill"


async def queue_bill_fulfillment(
    *,
    publisher: QueuePublisher | None,
    transaction: Any,
) -> bool:
    """Publish bill fulfillment wake-up after confirmed Mono debit."""
    if publisher is None:
        logger.error("bill_fulfillment_queue_unavailable", transaction_id=str(transaction.id))
        return False
    try:
        await publisher.publish(
            topic="bill.fulfill",
            message={
                "transaction_id": str(transaction.id),
                "idempotency_key": str(transaction.idempotency_key),
                "bill_reference": bill_reference_for_transaction(transaction),
            },
        )
        return True
    except Exception as exc:
        logger.error("bill_fulfillment_publish_failed", transaction_id=str(transaction.id), error=str(exc))
        return False


async def queue_transaction_debit_refund(
    *,
    publisher: QueuePublisher | None,
    debit_step: Any,
    transaction: Any,
) -> bool:
    """Publish transaction debit refund wake-up."""
    if publisher is None:
        logger.error("transaction_debit_refund_queue_unavailable", transaction_id=str(transaction.id))
        return False
    amount_naira = naira_to_json(debit_step.amount) or "0.00"
    try:
        await publisher.publish(
            topic="transaction_debit.refund",
            message={
                "transaction_debit_step_id": str(debit_step.id),
                "transaction_id": str(transaction.id),
                "amount": amount_naira,
                "amount_naira": amount_naira,
                "account_id": str(debit_step.account_id),
                "original_reference": debit_step.provider_reference,
            },
        )
        return True
    except Exception as exc:
        logger.error("transaction_debit_refund_publish_failed", transaction_id=str(transaction.id), error=str(exc))
        return False


async def apply_transaction_debit_result(
    *,
    uow: UnitOfWork,
    debit_step: Any,
    transaction: Any,
    result: DebitResult,
    reference: str,
    publisher: QueuePublisher | None,
) -> str:
    """Apply Mono debit result to a transaction debit step and transaction."""
    if not uow.transaction_debit_steps:
        return "skipped"
    if debit_step.status not in TRANSACTION_DEBIT_OPEN_STATUSES:
        return "skipped"

    if result.status == DebitStatus.FAILED and is_retryable_debit_result(result):
        retry_count = int(getattr(debit_step, "retry_count", 0) or 0) + 1
        debit_step.retry_count = retry_count
        if retry_count < int(settings.funding_step_max_retries):
            await uow.transaction_debit_steps.update_status(
                str(debit_step.id),
                TransactionDebitStepStatusEnum.PROCESSING.value,
                provider_reference=result.reference or reference,
                provider_debit_id=result.debit_id,
                error_message=result.error_message or "Transaction debit pending retry",
            )
            transaction.status = TransactionStatusEnum.PROCESSING.value
            transaction.provider_status = "debit_processing"
            transaction.provider_response = to_json_safe_dict(result.provider_response or {})
            if uow.db:
                uow.db.add(transaction)
                uow.db.add(debit_step)
            return "pending"
        result = DebitResult(
            success=False,
            status=DebitStatus.FAILED,
            reference=result.reference,
            debit_id=result.debit_id,
            amount=result.amount,
            error_message=result.error_message or "Transaction debit retry limit exhausted",
            provider_response=result.provider_response,
        )

    mapped_status = TransactionDebitStepStatusEnum.PROCESSING.value
    if result.status == DebitStatus.SUCCESSFUL:
        mapped_status = TransactionDebitStepStatusEnum.CONFIRMED.value
    elif result.status == DebitStatus.FAILED:
        mapped_status = TransactionDebitStepStatusEnum.FAILED.value

    updated_step = await uow.transaction_debit_steps.update_status(
        str(debit_step.id),
        mapped_status,
        provider_reference=result.reference or reference,
        provider_debit_id=result.debit_id,
        error_message=result.error_message,
    )
    debit_step = updated_step or debit_step

    transaction.provider_status = result.status.value
    transaction.provider_response = to_json_safe_dict(result.provider_response or {})
    if result.debit_id:
        transaction.transaction_id = result.debit_id
    if mapped_status == TransactionDebitStepStatusEnum.FAILED.value or not result.success:
        transaction.status = TransactionStatusEnum.FAILED.value
        transaction.error_message = result.error_message or "Account debit failed"
        transaction.completed_at = datetime.now(UTC).replace(tzinfo=None)
        if uow.db:
            uow.db.add(transaction)
        logger.warning("transaction_debit_failed", transaction_id=str(transaction.id), error=transaction.error_message)
        return "failed"

    transaction.status = TransactionStatusEnum.PROCESSING.value
    if uow.db:
        uow.db.add(transaction)

    if mapped_status == TransactionDebitStepStatusEnum.CONFIRMED.value:
        await LedgerPostingService.post_transaction_debit_confirmed(
            uow,
            debit_step,
            transaction,
            provider_reference=result.reference or reference,
        )
        await queue_bill_fulfillment(publisher=publisher, transaction=transaction)
        return "confirmed"

    return "pending"


async def finalize_transaction_debit_refund(
    *,
    uow: UnitOfWork,
    debit_step: Any,
    transaction: Any,
    result: DebitResult,
    refund_reference: str,
) -> str:
    """Apply Mono refund result to a transaction debit step and transaction."""
    if not uow.transaction_debit_steps:
        return "skipped"
    if debit_step.status not in TRANSACTION_DEBIT_REFUND_IN_FLIGHT:
        return "skipped"

    if result.debit_id:
        debit_step.refund_provider_id = result.debit_id
    debit_step.refund_provider_reference = result.reference or refund_reference
    debit_step.refund_last_checked_at = datetime.now(UTC).replace(tzinfo=None)
    if result.error_message:
        debit_step.refund_error_message = result.error_message
    if uow.db:
        uow.db.add(debit_step)

    if result.success and result.status == DebitStatus.REVERSED:
        updated_step = await uow.transaction_debit_steps.update_status(
            str(debit_step.id),
            TransactionDebitStepStatusEnum.REFUNDED.value,
        )
        debit_step = updated_step or debit_step
        await LedgerPostingService.post_transaction_debit_refund_confirmed(
            uow,
            debit_step,
            transaction,
            provider_reference=result.reference or refund_reference,
        )
        transaction.status = TransactionStatusEnum.REVERSED.value
        transaction.provider_status = "refunded"
        transaction.completed_at = datetime.now(UTC).replace(tzinfo=None)
        if uow.db:
            uow.db.add(transaction)
        return "refunded"

    if result.status == DebitStatus.FAILED:
        await uow.transaction_debit_steps.update_status(
            str(debit_step.id),
            TransactionDebitStepStatusEnum.REFUND_FAILED.value,
            error_message=result.error_message or "Transaction debit refund failed",
        )
        transaction.status = TransactionStatusEnum.FAILED.value
        transaction.error_message = result.error_message or "Refund failed; manual review required"
        transaction.provider_status = "refund_failed"
        if uow.db:
            uow.db.add(transaction)
        return "failed"

    await uow.transaction_debit_steps.update_status(
        str(debit_step.id),
        TransactionDebitStepStatusEnum.REFUND_PROCESSING.value,
        error_message=result.error_message or "Transaction debit refund pending",
    )
    return "pending"
