"""Payout consumer for final one-go beneficiary transfer."""

from datetime import UTC, datetime
from typing import Any

from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum
from shared.queue.adapter import QueuePublisher
from shared.repositories.unit_of_work import UnitOfWork
from shared.transaction_runtime.executors.payout import PayoutExecutor
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class PayoutConsumer:
    """Consumes payout jobs and executes a single beneficiary credit."""

    def __init__(self, payout_executor: PayoutExecutor, publisher: QueuePublisher | None = None):
        self.payout_executor = payout_executor
        self.publisher = publisher

    async def process_job(self, payload: dict[str, Any]) -> None:
        funded_transfer_id = payload.get("funded_transfer_id")
        if not funded_transfer_id:
            logger.warning("payout_job_missing_transfer_id", payload=payload)
            return

        async with UnitOfWork() as uow:
            transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id)) if uow.funded_transfers else None
            if not transfer:
                logger.warning("payout_transfer_not_found", funded_transfer_id=funded_transfer_id)
                return

            result = await self.payout_executor.handle_payout(payload)
            now = datetime.now(UTC).replace(tzinfo=None)
            transfer.payout_initiated_at = now
            transfer.payout_provider = self.payout_executor.payout_provider.provider_name
            transfer.payout_reference = result.get("transaction_id") or result.get("reference")

            tx = await uow.transactions.get_by_idempotency_key(transfer.idempotency_key) if uow.transactions else None

            status = str(result.get("status") or "").strip().lower()
            if result.get("success") and status in {"successful", "success", "completed"}:
                transfer.completed_at = now
                await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.COMPLETED.value)
                if tx:
                    tx.status = TransactionStatusEnum.SUCCESSFUL.value
                    tx.transaction_id = result.get("transaction_id") or tx.transaction_id
                    tx.provider_status = str(result.get("status") or "successful")
                    tx.provider_response = result
                    tx.completed_at = now
                    uow.db.add(tx)
                logger.info("payout_completed", funded_transfer_id=funded_transfer_id)
            elif status in {"pending", "processing", "queued", "new", "in_progress"}:
                await uow.funded_transfers.update_status(
                    str(transfer.id),
                    FundedTransferStatusEnum.PAYOUT_PENDING.value,
                )
                if tx:
                    tx.status = TransactionStatusEnum.PROCESSING.value
                    tx.provider_status = str(result.get("status") or "pending")
                    tx.provider_response = result
                    uow.db.add(tx)
                logger.warning(
                    "payout_pending",
                    funded_transfer_id=funded_transfer_id,
                    provider_status=result.get("provider_status"),
                )
            else:
                error = result.get("error") or "Payout failed"
                await uow.funded_transfers.update_status(
                    str(transfer.id),
                    FundedTransferStatusEnum.REFUNDING.value,
                    error_message=error,
                )
                if tx:
                    tx.status = TransactionStatusEnum.FAILED.value
                    tx.error_message = error
                    tx.provider_status = str(result.get("status") or "failed")
                    tx.provider_response = result
                    uow.db.add(tx)
                await self._queue_refunds(uow, transfer)
                logger.error("payout_failed_refund_queued", funded_transfer_id=funded_transfer_id, error=error)

            uow.db.add(transfer)
            await uow.commit()

    async def _queue_refunds(self, uow: UnitOfWork, transfer: Any) -> None:
        """Queue refunds for confirmed Mono funding debits after terminal payout failure."""
        if not self.publisher or not uow.funding_steps:
            logger.error("payout_refund_queue_unavailable", funded_transfer_id=str(getattr(transfer, "id", "")))
            raise RuntimeError("Refund queue is unavailable for failed payout")

        confirmed_steps = await uow.funding_steps.get_confirmed_for_transfer(str(transfer.id))
        if not confirmed_steps:
            logger.warning("payout_refund_no_confirmed_steps", funded_transfer_id=str(transfer.id))
            return

        for step in confirmed_steps:
            await self.publisher.publish(
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
