"""Refund consumer for reversing successful funding debits after failure."""

import asyncio
from typing import Any

from shared.clients.abstractions.direct_debit import DebitStatus, DirectDebitProvider
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class RefundConsumer:
    """Consumes refund jobs and attempts to reverse previously successful debits."""

    def __init__(self, redis_queue: RedisQueue, direct_debit_provider: DirectDebitProvider):
        self.queue = redis_queue
        self.direct_debit_provider = direct_debit_provider
        self.running = False

    async def process_job(self, payload: dict[str, Any]) -> None:
        funding_step_id = payload.get("funding_step_id")
        funded_transfer_id = payload.get("funded_transfer_id")
        if not funding_step_id or not funded_transfer_id:
            logger.warning("refund_job_missing_ids", payload=payload)
            return

        async with UnitOfWork() as uow:
            if not uow.funding_steps or not uow.funded_transfers:
                return

            step = await uow.funding_steps.get_by_id(str(funding_step_id))
            if not step:
                logger.warning("refund_step_not_found", funding_step_id=funding_step_id)
                return

            if step.status == FundingStepStatusEnum.REFUNDED.value:
                return

            if not step.provider_debit_id:
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.REFUND_PENDING.value,
                    error_message="Provider debit id missing for refund",
                )
                await uow.commit()
                return

            result = await self.direct_debit_provider.reverse_debit(step.provider_debit_id, reason="Funding refund")
            if result.success and result.status == DebitStatus.REVERSED:
                await uow.funding_steps.update_status(str(step.id), FundingStepStatusEnum.REFUNDED.value)
                logger.info("refund_completed", funding_step_id=funding_step_id)
            else:
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.REFUND_PENDING.value,
                    error_message=result.error_message or "Refund pending",
                )
                logger.warning("refund_pending", funding_step_id=funding_step_id, error=result.error_message)

            steps = await uow.funding_steps.get_by_transfer(str(funded_transfer_id))
            if steps:
                all_closed = all(
                    s.status in (FundingStepStatusEnum.REFUNDED.value, FundingStepStatusEnum.FAILED.value) for s in steps
                )
                all_refunded = all(s.status == FundingStepStatusEnum.REFUNDED.value for s in steps)
                if all_refunded:
                    await uow.funded_transfers.update_status(
                        str(funded_transfer_id),
                        FundedTransferStatusEnum.REFUNDED.value,
                    )
                elif all_closed:
                    await uow.funded_transfers.update_status(
                        str(funded_transfer_id),
                        FundedTransferStatusEnum.FAILED.value,
                    )

            await uow.commit()

    async def start(self, queue_name: str = "banking:refunds") -> None:
        """Start consuming refund jobs."""
        self.running = True
        logger.info("refund_consumer_started", queue_name=queue_name)
        await self.queue.connect()

        while self.running:
            try:
                job_data = await self.queue.dequeue_blocking(queue_name=queue_name, timeout=5)
                if job_data:
                    await self.process_job(job_data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("refund_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)

        await self.queue.close()

    def stop(self) -> None:
        """Stop refund consumer."""
        self.running = False
