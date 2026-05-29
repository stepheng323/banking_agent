"""Funding consumer for initiating pooled direct-debit steps."""

from datetime import UTC, datetime
from typing import Any

from shared.clients.abstractions.direct_debit import DebitStatus, DirectDebitProvider
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum
from shared.queue.adapter import QueuePublisher
from banking.persistence.unit_of_work import UnitOfWork
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

        failed = False
        failure_reason: str | None = None

        async with UnitOfWork() as uow:
            transfer = await uow.funded_transfers.get_by_id(str(funded_transfer_id)) if uow.funded_transfers else None
            if not transfer or not uow.funding_steps or not uow.accounts:
                logger.warning("funding_transfer_not_found", funded_transfer_id=funded_transfer_id)
                return

            steps = await uow.funding_steps.get_by_transfer(str(funded_transfer_id))
            if not steps:
                logger.warning("funding_steps_not_found", funded_transfer_id=funded_transfer_id)
                return

            for step in steps:
                if step.status not in (FundingStepStatusEnum.PENDING.value, FundingStepStatusEnum.PROCESSING.value):
                    continue

                account = await uow.accounts.get_by_id(str(step.account_id))
                if not account or not account.mandate_id:
                    failed = True
                    failure_reason = "Mandate not available for one funding account"
                    await uow.funding_steps.update_status(
                        str(step.id),
                        FundingStepStatusEnum.FAILED.value,
                        error_message=failure_reason,
                    )
                    break

                reference = step.provider_reference or f"{transfer.idempotency_key}-s{step.sequence}"
                result = await self.direct_debit_provider.initiate_pooling_debit(
                    mandate_id=account.mandate_id,
                    amount=float(step.amount),
                    reference=reference,
                    narration=transfer.narration or "Transfer funding",
                )

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
                    failed = True
                    failure_reason = result.error_message or "Funding debit initiation failed"
                    break

            if failed:
                await uow.funded_transfers.update_status(
                    str(transfer.id),
                    FundedTransferStatusEnum.REFUNDING.value,
                    error_message=failure_reason,
                )
                confirmed_steps = await uow.funding_steps.get_confirmed_for_transfer(str(transfer.id))
                for confirmed_step in confirmed_steps:
                    await self.publisher.publish(
                        topic="refund.process",
                        message={
                            "funding_step_id": str(confirmed_step.id),
                            "funded_transfer_id": str(transfer.id),
                            "amount": float(confirmed_step.amount),
                            "account_id": str(confirmed_step.account_id),
                            "original_reference": confirmed_step.provider_reference,
                        },
                    )
                    await uow.funding_steps.update_status(
                        str(confirmed_step.id),
                        FundingStepStatusEnum.REFUND_PENDING.value,
                    )
                await uow.commit()
                logger.warning("funding_job_failed", funded_transfer_id=funded_transfer_id, error=failure_reason)
                return

            all_confirmed = await uow.funding_steps.all_confirmed(str(transfer.id))
            if all_confirmed:
                transfer.funding_completed_at = datetime.now(UTC).replace(tzinfo=None)
                await uow.funded_transfers.update_status(
                    str(transfer.id), FundedTransferStatusEnum.PAYOUT_PENDING.value
                )
                await self.publisher.publish(
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
                        "narration": transfer.narration,
                    },
                )

            await uow.commit()
            logger.info("funding_job_processed", funded_transfer_id=funded_transfer_id, all_confirmed=all_confirmed)
