"""Funding consumer for initiating pooled direct-debit steps."""

from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.funding_status import (
    is_retryable_debit_result,
    queue_payout_if_all_confirmed,
    queue_refunds_for_confirmed_funding_steps,
)
from shared.clients.abstractions.direct_debit import DebitStatus, DirectDebitProvider
from shared.config.settings import settings
from shared.database.enums import FundingStepStatusEnum
from shared.money import naira_to_json, require_naira
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FundingConsumer:
    """Consumes funding jobs and starts Mono direct debits for each funding step."""

    def __init__(self, publisher: QueuePublisher, direct_debit_provider: DirectDebitProvider):
        self.publisher = publisher
        self.direct_debit_provider = direct_debit_provider

    async def process_job(self, payload: dict[str, Any]) -> None:
        funded_transfer_id = payload.get("funded_transfer_id")
        if not funded_transfer_id:
            logger.warning("funding_job_missing_transfer_id", payload=payload)
            return

        async with UnitOfWork() as uow:
            transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id)) if uow.funded_transfers else None
            if not transfer or not uow.funding_steps or not uow.accounts:
                logger.warning("funding_transfer_not_found", funded_transfer_id=funded_transfer_id)
                return

            steps = await uow.funding_steps.get_by_transfer(str(funded_transfer_id))
            if not steps:
                logger.warning("funding_steps_not_found", funded_transfer_id=funded_transfer_id)
                return

            all_confirmed = await uow.funding_steps.all_confirmed(str(transfer.id))
            if all_confirmed:
                await queue_payout_if_all_confirmed(uow=uow, transfer=transfer, publisher=self.publisher)
                await uow.commit()
                logger.info("funding_job_processed", funded_transfer_id=funded_transfer_id, all_confirmed=True)
                return

            step_ids = [
                str(step.id)
                for step in steps
                if step.status in (FundingStepStatusEnum.PENDING.value, FundingStepStatusEnum.PROCESSING.value)
            ]

        for step_id in step_ids:
            outcome = await self._process_step(str(funded_transfer_id), step_id)
            if outcome == "failed":
                return

        async with UnitOfWork() as uow:
            transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id)) if uow.funded_transfers else None
            if not transfer or not uow.funding_steps:
                return
            all_confirmed = await uow.funding_steps.all_confirmed(str(transfer.id))
            if all_confirmed:
                await queue_payout_if_all_confirmed(uow=uow, transfer=transfer, publisher=self.publisher)
            await uow.commit()
            logger.info("funding_job_processed", funded_transfer_id=funded_transfer_id, all_confirmed=all_confirmed)

    async def _process_step(self, funded_transfer_id: str, step_id: str) -> str:
        claim = await self._claim_step(funded_transfer_id, step_id)
        if claim is None:
            return "skipped"
        if claim.get("failed"):
            return "failed"

        result = await self.direct_debit_provider.initiate_pooling_debit(
            mandate_id=claim["mandate_id"],
            amount=claim["amount"],
            reference=claim["reference"],
            narration=claim["narration"],
        )
        return await self._apply_step_result(
            funded_transfer_id=funded_transfer_id,
            step_id=step_id,
            reference=claim["reference"],
            result=result,
        )

    async def _claim_step(self, funded_transfer_id: str, step_id: str) -> dict[str, Any] | None:
        async with UnitOfWork() as uow:
            if not uow.funding_steps or not uow.funded_transfers or not uow.accounts:
                return None
            transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id))
            if not transfer:
                return None
            get_step = getattr(uow.funding_steps, "get_by_id_for_update", uow.funding_steps.get_by_id)
            step = await get_step(step_id)
            if not step or step.status != FundingStepStatusEnum.PENDING.value:
                return None

            account = await uow.accounts.get_by_id(str(step.account_id))
            if not account or not account.mandate_id:
                failure_reason = "Mandate not available for one funding account"
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.FAILED.value,
                    error_message=failure_reason,
                )
                await queue_refunds_for_confirmed_funding_steps(
                    uow=uow,
                    transfer=transfer,
                    publisher=self.publisher,
                    error_message=failure_reason,
                )
                await uow.commit()
                logger.warning("funding_job_failed", funded_transfer_id=funded_transfer_id, error=failure_reason)
                return {"failed": True}

            reference = step.provider_reference or f"{transfer.idempotency_key}-s{step.sequence}"
            claim_method = getattr(uow.funding_steps, "claim_for_debit", None)
            if claim_method:
                claimed = await claim_method(str(step.id), provider_reference=reference)
            else:
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.PROCESSING.value,
                    provider_reference=reference,
                )
                claimed = step
            if not claimed:
                return None
            await uow.commit()
            amount = require_naira(step.amount)
            amount_naira = naira_to_json(amount) or "0.00"
            return {
                "mandate_id": account.mandate_id,
                "amount": amount,
                "amount_naira": amount_naira,
                "reference": reference,
                "narration": transfer.narration or "Transfer funding",
            }
        return None

    async def _apply_step_result(
        self,
        *,
        funded_transfer_id: str,
        step_id: str,
        reference: str,
        result: Any,
    ) -> str:
        async with UnitOfWork() as uow:
            if not uow.funding_steps or not uow.funded_transfers:
                return "skipped"
            get_step = getattr(uow.funding_steps, "get_by_id_for_update", uow.funding_steps.get_by_id)
            step = await get_step(step_id)
            transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id))
            if not step or not transfer:
                return "skipped"
            if step.status not in (FundingStepStatusEnum.PENDING.value, FundingStepStatusEnum.PROCESSING.value):
                logger.info("funding_result_skipped_terminal_step", funding_step_id=step_id, status=step.status)
                return "skipped"

            if result.status == DebitStatus.FAILED and is_retryable_debit_result(result):
                kept_open = await self._apply_retryable_result(uow, step, result)
                if kept_open:
                    await uow.commit()
                    return "pending"
                failure_reason = result.error_message or "Funding retry limit exhausted"
                await queue_refunds_for_confirmed_funding_steps(
                    uow=uow,
                    transfer=transfer,
                    publisher=self.publisher,
                    error_message=failure_reason,
                )
                await uow.commit()
                logger.warning("funding_job_failed", funded_transfer_id=funded_transfer_id, error=failure_reason)
                return "failed"

            mapped_status = FundingStepStatusEnum.PROCESSING.value
            if result.status == DebitStatus.SUCCESSFUL:
                mapped_status = FundingStepStatusEnum.CONFIRMED.value
            elif result.status == DebitStatus.FAILED:
                mapped_status = FundingStepStatusEnum.FAILED.value

            await uow.funding_steps.update_status(
                str(step.id),
                mapped_status,
                provider_reference=result.reference or reference,
                provider_debit_id=result.debit_id,
                error_message=result.error_message,
            )

            if not result.success or mapped_status == FundingStepStatusEnum.FAILED.value:
                failure_reason = result.error_message or "Funding debit initiation failed"
                await queue_refunds_for_confirmed_funding_steps(
                    uow=uow,
                    transfer=transfer,
                    publisher=self.publisher,
                    error_message=failure_reason,
                )
                await uow.commit()
                logger.warning("funding_job_failed", funded_transfer_id=funded_transfer_id, error=failure_reason)
                return "failed"

            if await uow.funding_steps.all_confirmed(str(transfer.id)):
                await queue_payout_if_all_confirmed(uow=uow, transfer=transfer, publisher=self.publisher)
            await uow.commit()
            return "processed"
        return "skipped"

    async def _apply_retryable_result(self, uow: UnitOfWork, step: Any, result: Any) -> bool:
        """Keep a transient funding outcome open until reconciliation exhausts attempts."""
        if not is_retryable_debit_result(result):
            return False

        retry_count = int(getattr(step, "retry_count", 0) or 0) + 1
        step.retry_count = retry_count
        if uow.db is not None:
            uow.db.add(step)

        assert uow.funding_steps is not None
        if retry_count >= int(settings.funding_step_max_retries):
            await uow.funding_steps.update_status(
                str(step.id),
                FundingStepStatusEnum.FAILED.value,
                provider_reference=result.reference,
                provider_debit_id=result.debit_id,
                error_message=result.error_message or "Funding retry limit exhausted",
            )
            return False

        await uow.funding_steps.update_status(
            str(step.id),
            FundingStepStatusEnum.PROCESSING.value,
            provider_reference=result.reference,
            provider_debit_id=result.debit_id,
            error_message=result.error_message or "Funding debit pending retry",
        )
        return True
