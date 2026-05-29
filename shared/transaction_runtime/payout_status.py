"""Shared payout status application for funded transfers."""

from datetime import UTC, datetime
from typing import Any

from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum
from shared.queue.adapter import QueuePublisher
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TERMINAL_PAYOUT_SUCCESS_STATUSES = frozenset({"success", "successful", "completed", "succeeded"})
TERMINAL_PAYOUT_FAILURE_STATUSES = frozenset({"failed", "failure", "cancelled", "canceled", "reversed"})
NON_TERMINAL_PAYOUT_STATUSES = frozenset({"new", "pending", "processing", "queued", "in_progress"})


def normalize_payout_status(status: Any) -> str:
    """Normalize provider payout status into successful, failed, or pending."""
    raw_status = str(status or "").strip().lower()
    if raw_status in TERMINAL_PAYOUT_SUCCESS_STATUSES:
        return "successful"
    if raw_status in TERMINAL_PAYOUT_FAILURE_STATUSES:
        return "failed"
    if raw_status in NON_TERMINAL_PAYOUT_STATUSES:
        return "pending"
    return "pending"


def payout_reference_from_result(result: dict[str, Any]) -> str | None:
    """Return the provider transfer identifier/reference worth storing."""
    for key in ("transaction_id", "transfer_id", "id", "reference"):
        value = result.get(key)
        if value:
            return str(value)
    return None


async def queue_refunds_for_confirmed_funding_steps(
    *,
    uow: UnitOfWork,
    transfer: Any,
    publisher: QueuePublisher | None,
) -> None:
    """Queue refunds for confirmed Mono funding debits after terminal payout failure."""
    if not publisher or not uow.funding_steps:
        logger.error("payout_refund_queue_unavailable", funded_transfer_id=str(getattr(transfer, "id", "")))
        raise RuntimeError("Refund queue is unavailable for failed payout")

    confirmed_steps = await uow.funding_steps.get_confirmed_for_transfer(str(transfer.id))
    if not confirmed_steps:
        logger.warning("payout_refund_no_confirmed_steps", funded_transfer_id=str(transfer.id))
        return

    for step in confirmed_steps:
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
        await uow.funding_steps.update_status(str(step.id), FundingStepStatusEnum.REFUND_PENDING.value)
        logger.info("payout_refund_queued", funding_step_id=str(step.id), funded_transfer_id=str(transfer.id))


async def apply_payout_result(
    *,
    uow: UnitOfWork,
    transfer: Any,
    result: dict[str, Any],
    publisher: QueuePublisher | None = None,
    provider_name: str | None = None,
    mark_payout_initiated: bool = False,
) -> str:
    """Apply a verified payout result to a funded transfer and linked transaction."""
    now = datetime.now(UTC).replace(tzinfo=None)
    if mark_payout_initiated:
        transfer.payout_initiated_at = now
    if provider_name:
        transfer.payout_provider = provider_name

    payout_reference = payout_reference_from_result(result)
    if payout_reference:
        transfer.payout_reference = payout_reference

    tx = await uow.transactions.get_by_idempotency_key(transfer.idempotency_key) if uow.transactions else None
    status = normalize_payout_status(result.get("status") or result.get("provider_status"))

    if status == "successful" and result.get("success") is not False:
        transfer.completed_at = now
        await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.COMPLETED.value)
        if tx:
            tx.status = TransactionStatusEnum.SUCCESSFUL.value
            tx.transaction_id = payout_reference or tx.transaction_id
            tx.provider_status = str(result.get("provider_status") or result.get("status") or "successful")
            tx.provider_response = result
            tx.completed_at = now
            uow.db.add(tx)
        logger.info("payout_completed", funded_transfer_id=str(transfer.id))
        return "completed"

    if status == "pending":
        await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.PAYOUT_PENDING.value)
        if tx:
            tx.status = TransactionStatusEnum.PROCESSING.value
            tx.provider_status = str(result.get("provider_status") or result.get("status") or "pending")
            tx.provider_response = result
            uow.db.add(tx)
        logger.warning(
            "payout_pending",
            funded_transfer_id=str(transfer.id),
            provider_status=result.get("provider_status") or result.get("status"),
        )
        return "pending"

    error = result.get("error") or "Payout failed"
    await uow.funded_transfers.update_status(
        str(transfer.id),
        FundedTransferStatusEnum.REFUNDING.value,
        error_message=error,
    )
    if tx:
        tx.status = TransactionStatusEnum.FAILED.value
        tx.error_message = error
        tx.provider_status = str(result.get("provider_status") or result.get("status") or "failed")
        tx.provider_response = result
        uow.db.add(tx)
    await queue_refunds_for_confirmed_funding_steps(uow=uow, transfer=transfer, publisher=publisher)
    logger.error("payout_failed_refund_queued", funded_transfer_id=str(transfer.id), error=error)
    return "failed"
