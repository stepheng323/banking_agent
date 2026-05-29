"""Payout consumer for final one-go beneficiary transfer."""

from typing import Any

from shared.queue.adapter import QueuePublisher
from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.executors.payout import PayoutExecutor
from banking.transactions.runtime.payout_status import apply_payout_result
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
            await apply_payout_result(
                uow=uow,
                transfer=transfer,
                result=result,
                publisher=self.publisher,
                provider_name=self.payout_executor.payout_provider.provider_name,
                mark_payout_initiated=True,
            )
            uow.db.add(transfer)
            await uow.commit()
