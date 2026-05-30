"""Funding/refund state helpers for pooled transfers."""

from datetime import UTC, datetime
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)

FUNDING_PAYOUT_CLOSED_STATUSES = frozenset(
    {
        FundedTransferStatusEnum.PAYOUT_PENDING.value,
        FundedTransferStatusEnum.REVIEW_PENDING.value,
        FundedTransferStatusEnum.COMPLETED.value,
        FundedTransferStatusEnum.REFUNDING.value,
        FundedTransferStatusEnum.REFUNDED.value,
        FundedTransferStatusEnum.FAILED.value,
    }
)

REFUND_IN_FLIGHT_STATUSES = frozenset(
    {
        FundingStepStatusEnum.REFUND_PENDING.value,
        FundingStepStatusEnum.REFUND_PROCESSING.value,
    }
)

REFUND_RECOVERY_STATUSES = frozenset(
    {
        FundingStepStatusEnum.CONFIRMED.value,
        FundingStepStatusEnum.REFUND_PENDING.value,
        FundingStepStatusEnum.REFUND_PROCESSING.value,
        FundingStepStatusEnum.REFUND_FAILED.value,
        FundingStepStatusEnum.REFUNDED.value,
    }
)


def is_retryable_debit_result(result: DebitResult) -> bool:
    """Return whether a Mono debit outcome should stay open for reconciliation."""
    if result.status in {DebitStatus.PENDING, DebitStatus.PROCESSING}:
        return True

    response = result.provider_response or {}
    if "http_status" in response:
        try:
            http_status = int(response.get("http_status") or 0)
        except (TypeError, ValueError):
            http_status = 0
        if http_status == 0 or http_status == 429 or http_status >= 500:
            return True

    error_text = str(result.error_message or response.get("message") or "").lower()
    return any(token in error_text for token in ("timeout", "connection", "rate limit", "temporarily unavailable"))


async def queue_payout_if_all_confirmed(
    *,
    uow: UnitOfWork,
    transfer: Any,
    publisher: QueuePublisher,
) -> bool:
    """Queue payout once all funding steps are confirmed."""
    if not uow.funding_steps or not uow.funded_transfers:
        return False
    if transfer.status in FUNDING_PAYOUT_CLOSED_STATUSES:
        logger.info("payout_already_queued_or_closed", transfer_id=str(transfer.id), status=transfer.status)
        return False
    if not await uow.funding_steps.all_confirmed(str(transfer.id)):
        return False

    transfer.funding_completed_at = datetime.now(UTC).replace(tzinfo=None)
    await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.PAYOUT_PENDING.value)
    try:
        await publisher.publish(
            topic="payout.process",
            message={
                "funded_transfer_id": str(transfer.id),
                "amount": float(transfer.amount),
                "recipient_account": transfer.recipient_account_number,
                "recipient_bank_code": transfer.recipient_bank_code,
                "recipient_bank_code_provider": transfer.payout_provider or "flutterwave",
                "recipient_resolution_provider": transfer.payout_provider or "flutterwave",
                "payout_provider": transfer.payout_provider or "flutterwave",
                "idempotency_key": transfer.idempotency_key,
                "narration": getattr(transfer, "narration", None),
            },
        )
    except Exception as exc:
        logger.error("payout_publish_failed_reconciliation_will_retry", transfer_id=str(transfer.id), error=str(exc))
    logger.info("all_debits_complete", transfer_id=str(transfer.id))
    return True


async def mark_transaction_failed(uow: UnitOfWork, transfer: Any, error_message: str | None) -> None:
    """Mark the user-facing transaction failed while recovery is incomplete."""
    transactions = getattr(uow, "transactions", None)
    if not transactions:
        return
    tx = await transactions.get_by_idempotency_key(transfer.idempotency_key)
    if not tx:
        return
    tx.status = TransactionStatusEnum.FAILED.value
    tx.error_message = error_message or tx.error_message
    tx.provider_status = tx.provider_status or "funding_failed"
    if uow.db:
        uow.db.add(tx)


async def queue_refunds_for_confirmed_funding_steps(
    *,
    uow: UnitOfWork,
    transfer: Any,
    publisher: QueuePublisher | None,
    error_message: str | None = None,
) -> int:
    """Queue one refund job for each still-confirmed funding debit."""
    if not uow.funding_steps:
        return 0
    if not publisher:
        logger.error(
            "refund_queue_unavailable_reconciliation_will_retry",
            funded_transfer_id=str(getattr(transfer, "id", "")),
        )

    confirmed_steps = await uow.funding_steps.get_confirmed_for_transfer(str(transfer.id))
    if not confirmed_steps:
        steps = await uow.funding_steps.get_by_transfer(str(transfer.id))
        if any(step.status in REFUND_IN_FLIGHT_STATUSES for step in steps):
            logger.info("refunds_already_pending", funded_transfer_id=str(transfer.id))
            return 0
        if uow.funded_transfers:
            await uow.funded_transfers.update_status(
                str(transfer.id),
                FundedTransferStatusEnum.FAILED.value,
                error_message=error_message,
            )
        await mark_transaction_failed(uow, transfer, error_message)
        logger.info("no_refunds_needed", funded_transfer_id=str(transfer.id))
        return 0

    if uow.funded_transfers and transfer.status != FundedTransferStatusEnum.REFUNDING.value:
        await uow.funded_transfers.update_status(
            str(transfer.id),
            FundedTransferStatusEnum.REFUNDING.value,
            error_message=error_message,
        )
    await mark_transaction_failed(uow, transfer, error_message)

    queued = 0
    for step in confirmed_steps:
        await uow.funding_steps.update_status(str(step.id), FundingStepStatusEnum.REFUND_PENDING.value)
        if publisher:
            try:
                await publisher.publish(
                    topic="refund.process",
                    message={
                        "funding_step_id": str(step.id),
                        "funded_transfer_id": str(transfer.id),
                        "amount": float(step.amount),
                        "account_id": str(step.account_id),
                        "original_reference": step.provider_reference,
                    },
                )
                queued += 1
            except Exception as exc:
                logger.error(
                    "refund_publish_failed_reconciliation_will_retry",
                    funding_step_id=str(step.id),
                    funded_transfer_id=str(transfer.id),
                    error=str(exc),
                )
        logger.info("refund_queued", funding_step_id=str(step.id), funded_transfer_id=str(transfer.id))
    return queued


async def finalize_refund_state(uow: UnitOfWork, transfer: Any) -> str | None:
    """Close transfer/transaction once every debited funding leg has a refund outcome."""
    if not uow.funding_steps or not uow.funded_transfers:
        return None
    steps = await uow.funding_steps.get_by_transfer(str(transfer.id))
    debited_steps = [
        step
        for step in steps
        if step.status in REFUND_RECOVERY_STATUSES
        or getattr(step, "confirmed_at", None)
        or getattr(step, "refund_provider_id", None)
    ]
    if not debited_steps:
        return None

    if all(step.status == FundingStepStatusEnum.REFUNDED.value for step in debited_steps):
        await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.REFUNDED.value)
        transactions = getattr(uow, "transactions", None)
        if transactions:
            tx = await transactions.get_by_idempotency_key(transfer.idempotency_key)
            if tx:
                tx.status = TransactionStatusEnum.REVERSED.value
                tx.provider_status = "refunded"
                tx.completed_at = datetime.now(UTC).replace(tzinfo=None)
                if uow.db:
                    uow.db.add(tx)
        logger.info("funded_transfer_refunded", funded_transfer_id=str(transfer.id))
        return "refunded"

    if any(step.status == FundingStepStatusEnum.REFUND_FAILED.value for step in debited_steps) and not any(
        step.status in REFUND_IN_FLIGHT_STATUSES or step.status == FundingStepStatusEnum.CONFIRMED.value
        for step in debited_steps
    ):
        await uow.funded_transfers.update_status(
            str(transfer.id),
            FundedTransferStatusEnum.FAILED.value,
            error_message="Refund failed; manual review required",
        )
        await mark_transaction_failed(uow, transfer, "Refund failed; manual review required")
        logger.error("funded_transfer_refund_failed", funded_transfer_id=str(transfer.id))
        return "failed"

    return "pending"
