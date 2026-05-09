"""Payout consumer for final one-go beneficiary transfer."""

from datetime import UTC, datetime
from typing import Any

from apps.chat.src.agent.executors.payout import PayoutExecutor
from shared.database.enums import FundedTransferStatusEnum, TransactionStatusEnum
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class PayoutConsumer:
    """Consumes payout jobs and executes a single beneficiary credit."""

    def __init__(self, payout_executor: PayoutExecutor):
        self.payout_executor = payout_executor

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

            if result.get("success"):
                transfer.completed_at = now
                await uow.funded_transfers.update_status(str(transfer.id), FundedTransferStatusEnum.COMPLETED.value)
                if tx:
                    tx.status = TransactionStatusEnum.SUCCESSFUL.value
                    tx.transaction_id = result.get("transaction_id") or tx.transaction_id
                    tx.provider_status = str(result.get("status") or "success")
                    tx.provider_response = result
                    tx.completed_at = now
                    uow.db.add(tx)
                logger.info("payout_completed", funded_transfer_id=funded_transfer_id)
            else:
                error = result.get("error") or "Payout failed"
                await uow.funded_transfers.update_status(
                    str(transfer.id),
                    FundedTransferStatusEnum.FAILED.value,
                    error_message=error,
                )
                if tx:
                    tx.status = TransactionStatusEnum.FAILED.value
                    tx.error_message = error
                    tx.provider_status = str(result.get("status") or "failed")
                    tx.provider_response = result
                    uow.db.add(tx)
                logger.error("payout_failed", funded_transfer_id=funded_transfer_id, error=error)

            uow.db.add(transfer)
            await uow.commit()
