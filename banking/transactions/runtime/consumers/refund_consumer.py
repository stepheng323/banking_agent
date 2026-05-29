"""Refund consumer for reversing successful funding debits after failure."""

from datetime import UTC, datetime
from typing import Any

from shared.clients.abstractions.direct_debit import DebitStatus, DirectDebitProvider
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum
from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.funding_status import finalize_refund_state
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class RefundConsumer:
    """Consumes refund jobs and attempts to reverse previously successful debits."""

    def __init__(self, direct_debit_provider: DirectDebitProvider):
        self.direct_debit_provider = direct_debit_provider

    async def process_job(self, payload: dict[str, Any]) -> None:
        funding_step_id = payload.get("funding_step_id")
        funded_transfer_id = payload.get("funded_transfer_id")
        if not funding_step_id or not funded_transfer_id:
            logger.warning("refund_job_missing_ids", payload=payload)
            return

        claim = await self._claim_refund(str(funding_step_id), str(funded_transfer_id), payload)
        if claim is None:
            return

        result = await self.direct_debit_provider.reverse_debit(str(claim["refund_reference"]), reason="Funding refund")

        async with UnitOfWork() as uow:
            if not uow.funding_steps or not uow.funded_transfers:
                return

            get_step = getattr(uow.funding_steps, "get_by_id_for_update", uow.funding_steps.get_by_id)
            step = await get_step(str(funding_step_id))
            if not step:
                logger.warning("refund_step_not_found", funding_step_id=funding_step_id)
                return

            if step.status in (FundingStepStatusEnum.REFUNDED.value, FundingStepStatusEnum.REFUND_FAILED.value):
                return

            if step.status not in (FundingStepStatusEnum.REFUND_PROCESSING.value, FundingStepStatusEnum.REFUND_PENDING.value):
                logger.info("refund_result_skipped_step_status", funding_step_id=funding_step_id, status=step.status)
                return

            self._store_refund_metadata(uow, step, result, str(claim["refund_reference"]))

            if result.success and result.status == DebitStatus.REVERSED:
                await uow.funding_steps.update_status(str(step.id), FundingStepStatusEnum.REFUNDED.value)
                logger.info("refund_completed", funding_step_id=funding_step_id)
            elif result.status == DebitStatus.FAILED:
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.REFUND_FAILED.value,
                    error_message=result.error_message or "Refund failed",
                )
                logger.error("refund_failed", funding_step_id=funding_step_id, error=result.error_message)
            else:
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.REFUND_PROCESSING.value,
                    error_message=result.error_message or "Refund pending",
                )
                logger.warning("refund_pending", funding_step_id=funding_step_id, error=result.error_message)

            transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id))
            if transfer:
                await finalize_refund_state(uow, transfer)

            await uow.commit()

    async def _claim_refund(
        self,
        funding_step_id: str,
        funded_transfer_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with UnitOfWork() as uow:
            if not uow.funding_steps:
                return None
            get_step = getattr(uow.funding_steps, "get_by_id_for_update", uow.funding_steps.get_by_id)
            step = await get_step(funding_step_id)
            if not step:
                logger.warning("refund_step_not_found", funding_step_id=funding_step_id)
                return None
            transfer = None
            if uow.funded_transfers:
                transfer = await uow.funded_transfers.get_by_id(funded_transfer_id)
            if step.status == FundingStepStatusEnum.CONFIRMED.value and (
                not transfer or transfer.status != FundedTransferStatusEnum.REFUNDING.value
            ):
                logger.warning(
                    "refund_claim_skipped_transfer_not_refunding",
                    funding_step_id=funding_step_id,
                    funded_transfer_id=funded_transfer_id,
                    transfer_status=getattr(transfer, "status", None),
                )
                return None
            if step.status in (FundingStepStatusEnum.REFUNDED.value, FundingStepStatusEnum.REFUND_FAILED.value):
                return None
            if getattr(step, "refund_provider_id", None) or getattr(step, "refund_initiated_at", None):
                logger.info("refund_already_initiated", funding_step_id=funding_step_id)
                return None

            refund_reference = step.provider_reference or payload.get("original_reference")
            if not refund_reference:
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.REFUND_FAILED.value,
                    error_message="Provider reference missing for refund",
                )
                await uow.commit()
                return None

            claim_method = getattr(uow.funding_steps, "claim_for_refund", None)
            if claim_method:
                claimed = await claim_method(str(step.id), refund_reference=str(refund_reference))
            else:
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.REFUND_PROCESSING.value,
                )
                claimed = step
            if not claimed:
                return None
            await uow.commit()
            return {
                "funding_step_id": funding_step_id,
                "funded_transfer_id": funded_transfer_id,
                "refund_reference": str(refund_reference),
            }

    @staticmethod
    def _store_refund_metadata(uow: UnitOfWork, step: Any, result: Any, refund_reference: str) -> None:
        """Persist refund provider metadata on the funding step when columns exist."""
        if result.debit_id:
            setattr(step, "refund_provider_id", result.debit_id)
        setattr(step, "refund_provider_reference", result.reference or refund_reference)
        if not getattr(step, "refund_initiated_at", None):
            setattr(step, "refund_initiated_at", datetime.now(UTC).replace(tzinfo=None))
        if result.error_message:
            setattr(step, "refund_error_message", result.error_message)
        if getattr(uow, "db", None) is not None:
            uow.db.add(step)
