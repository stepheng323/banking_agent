"""Payout consumer for final one-go beneficiary transfer."""

from typing import Any

from banking.persistence.unit_of_work import UnitOfWork
from banking.transactions.runtime.executors.payout import PayoutExecutor
from banking.transactions.runtime.payout_status import apply_payout_result
from shared.database.enums import FundedTransferStatusEnum
from shared.queue.adapter import QueuePublisher
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

        claim_payload = await self._claim_payout(str(funded_transfer_id), payload)
        if claim_payload is None:
            return

        result = await self.payout_executor.handle_payout(claim_payload)

        async with UnitOfWork() as uow:
            if not uow.funded_transfers:
                return
            get_transfer = getattr(uow.funded_transfers, "get_by_id_for_update", uow.funded_transfers.get_by_id)
            transfer = await get_transfer(str(funded_transfer_id))
            if not transfer:
                logger.warning("payout_transfer_not_found", funded_transfer_id=funded_transfer_id)
                return
            if transfer.status != FundedTransferStatusEnum.PAYOUT_PENDING.value:
                logger.info(
                    "payout_job_skipped_non_pending_transfer",
                    funded_transfer_id=str(transfer.id),
                    status=transfer.status,
                )
                return

            await apply_payout_result(
                uow=uow,
                transfer=transfer,
                result=result,
                publisher=self.publisher,
                provider_name=self.payout_executor.payout_provider.provider_name,
            )
            uow.db.add(transfer)
            await uow.commit()

    async def _claim_payout(self, funded_transfer_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with UnitOfWork() as uow:
            if not uow.funded_transfers:
                return None
            get_transfer = getattr(uow.funded_transfers, "get_by_id_for_update", uow.funded_transfers.get_by_id)
            transfer = await get_transfer(funded_transfer_id)
            if not transfer:
                logger.warning("payout_transfer_not_found", funded_transfer_id=funded_transfer_id)
                return None
            if transfer.status != FundedTransferStatusEnum.PAYOUT_PENDING.value:
                logger.info(
                    "payout_job_skipped_non_pending_transfer",
                    funded_transfer_id=str(transfer.id),
                    status=transfer.status,
                )
                return None
            if getattr(transfer, "payout_initiated_at", None):
                logger.info("payout_job_skipped_already_claimed", funded_transfer_id=str(transfer.id))
                return None

            reference = str(payload.get("idempotency_key") or transfer.idempotency_key)
            claim_method = getattr(uow.funded_transfers, "claim_for_payout", None)
            if claim_method:
                claimed = await claim_method(str(transfer.id), payout_reference=reference)
            else:
                await uow.funded_transfers.update_status(
                    str(transfer.id),
                    FundedTransferStatusEnum.PAYOUT_PENDING.value,
                )
                claimed = transfer
            if not claimed:
                return None
            await uow.commit()
            return {
                **payload,
                "funded_transfer_id": str(transfer.id),
                "amount": float(payload.get("amount") or getattr(transfer, "amount", 0.0) or 0.0),
                "recipient_account": payload.get("recipient_account")
                or getattr(transfer, "recipient_account_number", ""),
                "recipient_bank_code": payload.get("recipient_bank_code")
                or getattr(transfer, "recipient_bank_code", ""),
                "recipient_bank_code_provider": payload.get("recipient_bank_code_provider")
                or getattr(transfer, "payout_provider", None)
                or "flutterwave",
                "recipient_resolution_provider": payload.get("recipient_resolution_provider")
                or getattr(transfer, "payout_provider", None)
                or "flutterwave",
                "payout_provider": payload.get("payout_provider")
                or getattr(transfer, "payout_provider", None)
                or "flutterwave",
                "idempotency_key": reference,
                "narration": payload.get("narration") or getattr(transfer, "narration", None),
            }
        return None
