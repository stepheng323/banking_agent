"""Funding reconciliation consumer for stale Mono funding steps."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.funding_status import (
    is_retryable_debit_result,
    queue_payout_if_all_confirmed,
    queue_refunds_for_confirmed_funding_steps,
)
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus, DirectDebitProvider
from shared.config.settings import settings
from shared.database.enums import FundingStepStatusEnum
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FundingReconciliationTarget:
    """Funding step target for reconciliation."""

    funding_step_id: str
    funded_transfer_id: str


class FundingReconciliationConsumer:
    """Reconciles stale Mono funding steps and closes failed funding safely."""

    def __init__(self, direct_debit_provider: DirectDebitProvider, publisher: QueuePublisher):
        self.direct_debit_provider = direct_debit_provider
        self.publisher = publisher

    async def process_job(self, payload: dict[str, Any]) -> None:
        if payload.get("funding_step_id"):
            await self._reconcile_payload(payload)
            return
        await self._reconcile_batch(payload)

    async def _reconcile_batch(self, payload: dict[str, Any]) -> None:
        limit = int(payload.get("limit") or settings.funding_reconciliation_batch_size)
        min_age_seconds = int(payload.get("min_age_seconds") or settings.funding_reconciliation_min_age_seconds)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=min_age_seconds)

        async with UnitOfWork() as uow:
            if not uow.funding_steps:
                return
            get_steps = getattr(uow.funding_steps, "get_recoverable_open", uow.funding_steps.get_stale_processing)
            steps = await get_steps(cutoff=cutoff, limit=limit)
            targets = [
                FundingReconciliationTarget(
                    funding_step_id=str(step.id),
                    funded_transfer_id=str(step.funded_transfer_id),
                )
                for step in steps
            ]

        for target in targets:
            await self._reconcile_target(target)

    async def _reconcile_payload(self, payload: dict[str, Any]) -> None:
        funding_step_id = str(payload.get("funding_step_id") or "")
        if not funding_step_id:
            return
        funded_transfer_id = str(payload.get("funded_transfer_id") or "")

        async with UnitOfWork() as uow:
            if not uow.funding_steps:
                return
            step = await uow.funding_steps.get_by_id(funding_step_id)
            if not step:
                logger.warning("funding_reconciliation_step_not_found", funding_step_id=funding_step_id)
                return
            target = FundingReconciliationTarget(
                funding_step_id=str(step.id),
                funded_transfer_id=funded_transfer_id or str(step.funded_transfer_id),
            )

        await self._reconcile_target(target)

    async def _reconcile_target(self, target: FundingReconciliationTarget) -> None:
        async with UnitOfWork() as uow:
            if not uow.funding_steps or not uow.funded_transfers or not uow.accounts:
                return

            get_step = getattr(uow.funding_steps, "get_by_id_for_update", uow.funding_steps.get_by_id)
            step = await get_step(target.funding_step_id)
            if not step:
                logger.warning("funding_reconciliation_step_not_found", funding_step_id=target.funding_step_id)
                return
            if step.status not in (FundingStepStatusEnum.PENDING.value, FundingStepStatusEnum.PROCESSING.value):
                logger.info(
                    "funding_reconciliation_skipped_terminal_step",
                    funding_step_id=str(step.id),
                    status=step.status,
                )
                return

            transfer = await uow.funded_transfers.get_by_id(str(step.funded_transfer_id))
            if not transfer:
                logger.warning("funding_reconciliation_transfer_not_found", funding_step_id=str(step.id))
                return

            if step.provider_debit_id:
                provider_debit_id = str(step.provider_debit_id)
                await uow.commit()
                result = await self.direct_debit_provider.get_debit_status(provider_debit_id)
            else:
                claim = await self._claim_for_retry(uow, step, transfer)
                if claim is None:
                    return
                await uow.commit()
                result = await self.direct_debit_provider.initiate_pooling_debit(
                    mandate_id=claim["mandate_id"],
                    amount=claim["amount"],
                    reference=claim["reference"],
                    narration=claim["narration"],
                )

        async with UnitOfWork() as uow:
            if not uow.funding_steps or not uow.funded_transfers:
                return
            get_step = getattr(uow.funding_steps, "get_by_id_for_update", uow.funding_steps.get_by_id)
            step = await get_step(target.funding_step_id)
            if not step:
                return
            if step.status not in (FundingStepStatusEnum.PENDING.value, FundingStepStatusEnum.PROCESSING.value):
                logger.info(
                    "funding_reconciliation_result_skipped_terminal_step",
                    funding_step_id=str(step.id),
                    status=step.status,
                )
                return
            transfer = await uow.funded_transfers.get_by_id(str(step.funded_transfer_id))
            if not transfer:
                return
            await self._apply_result(uow, step, transfer, result)
            await uow.commit()

    async def _claim_for_retry(self, uow: UnitOfWork, step: Any, transfer: Any) -> dict[str, Any] | None:
        result = await self._retry_debit_initiation(uow, step, transfer)
        if result.status == DebitStatus.FAILED:
            await self._apply_result(uow, step, transfer, result)
            await uow.commit()
            return None
        provider_response = result.provider_response or {}
        mandate_id = provider_response.get("mandate_id")
        if not mandate_id:
            logger.info("funding_reconciliation_claim_lost_race", funding_step_id=str(step.id))
            return None
        return {
            "mandate_id": mandate_id,
            "amount": float(step.amount),
            "reference": result.reference or step.provider_reference or f"{transfer.idempotency_key}-s{step.sequence}",
            "narration": transfer.narration or "Transfer funding",
        }

    async def _retry_debit_initiation(self, uow: UnitOfWork, step: Any, transfer: Any) -> DebitResult:
        retry_count = int(getattr(step, "retry_count", 0) or 0)
        if retry_count >= int(settings.funding_step_max_retries):
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=step.provider_reference,
                error_message="Funding retry limit exhausted",
            )

        account = await uow.accounts.get_by_id(str(step.account_id))
        if not account or not account.mandate_id:
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=step.provider_reference,
                error_message="Mandate not available for one funding account",
            )

        reference = step.provider_reference or f"{transfer.idempotency_key}-s{step.sequence}"
        if step.status == FundingStepStatusEnum.PENDING.value:
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
                return DebitResult(success=True, status=DebitStatus.PROCESSING, reference=reference)
        else:
            await uow.funding_steps.update_status(
                str(step.id),
                FundingStepStatusEnum.PROCESSING.value,
                provider_reference=reference,
            )
        return DebitResult(
            success=True,
            status=DebitStatus.PROCESSING,
            reference=reference,
            amount=float(step.amount),
            provider_response={"mandate_id": account.mandate_id},
        )

    async def _apply_result(self, uow: UnitOfWork, step: Any, transfer: Any, result: DebitResult) -> None:
        if result.status == DebitStatus.SUCCESSFUL:
            await uow.funding_steps.update_status(
                str(step.id),
                FundingStepStatusEnum.CONFIRMED.value,
                provider_reference=result.reference,
                provider_debit_id=result.debit_id,
                error_message=result.error_message,
            )
            await queue_payout_if_all_confirmed(uow=uow, transfer=transfer, publisher=self.publisher)
            return

        if result.status in {DebitStatus.PENDING, DebitStatus.PROCESSING} or is_retryable_debit_result(result):
            retry_count = int(getattr(step, "retry_count", 0) or 0) + 1
            step.retry_count = retry_count
            if getattr(uow, "db", None) is not None:
                uow.db.add(step)

            if retry_count < int(settings.funding_step_max_retries):
                await uow.funding_steps.update_status(
                    str(step.id),
                    FundingStepStatusEnum.PROCESSING.value,
                    provider_reference=result.reference,
                    provider_debit_id=result.debit_id,
                    error_message=result.error_message or "Funding debit still pending",
                )
                logger.info("funding_reconciliation_still_pending", funding_step_id=str(step.id))
                return

            result = DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=result.reference,
                debit_id=result.debit_id,
                error_message=result.error_message or "Funding retry limit exhausted",
                provider_response=result.provider_response,
            )

        await uow.funding_steps.update_status(
            str(step.id),
            FundingStepStatusEnum.FAILED.value,
            provider_reference=result.reference,
            provider_debit_id=result.debit_id,
            error_message=result.error_message or "Funding debit failed",
        )
        await queue_refunds_for_confirmed_funding_steps(
            uow=uow,
            transfer=transfer,
            publisher=self.publisher,
            error_message=result.error_message or "Funding debit failed",
        )
        logger.warning("funding_reconciliation_failed", funding_step_id=str(step.id), error=result.error_message)
