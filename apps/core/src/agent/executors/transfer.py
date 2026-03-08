"""Transfer Executor.

Handles execution of transfer transactions from the queue.
"""

from typing import Any

from shared.clients.abstractions.direct_debit import DebitStatus, DirectDebitProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.queue.adapter import QueuePublisher
from shared.repositories.account_repository import AccountRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.delivery_service import DeliveryService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferExecutor:
    """Executor for Transfer transactions."""

    def __init__(
        self,
        direct_debit_provider: DirectDebitProvider,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
        publisher: QueuePublisher | None = None,
        delivery_service: DeliveryService | None = None,
    ):
        self.direct_debit_provider = direct_debit_provider
        self.account_repo = account_repo
        self.transaction_repo = transaction_repo
        self.publisher = publisher
        self.delivery_service = delivery_service

    def _resolve_delivery_service(self) -> DeliveryService | None:
        if self.delivery_service is not None:
            return self.delivery_service
        try:
            self.delivery_service = DeliveryService()
            return self.delivery_service
        except Exception as exc:
            logger.warning("delivery_service_unavailable", error=str(exc))
            return None

    @staticmethod
    def _scheduled_meta(data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("scheduled_meta")
        return raw if isinstance(raw, dict) else {}

    async def _update_scheduled_run(
        self,
        schedule_run_id: str | None,
        *,
        status: str,
        error_message: str | None = None,
        transaction_id: str | None = None,
        attempt: int | None = None,
    ) -> None:
        if not schedule_run_id:
            return
        try:
            async with UnitOfWork() as uow:
                if not uow.scheduled_runs:
                    return
                run = await uow.scheduled_runs.get_by_id(schedule_run_id)
                if not run:
                    return
                run.status = status
                run.error_message = error_message
                run.transaction_id = transaction_id or run.transaction_id
                if attempt is not None:
                    run.attempt = attempt
                if status in {"successful", "failed"}:
                    from datetime import UTC, datetime

                    run.completed_at = datetime.now(UTC).replace(tzinfo=None)
                uow.db.add(run)
                await uow.commit()
        except Exception as exc:
            logger.warning("scheduled_run_update_failed", schedule_run_id=schedule_run_id, error=str(exc))

    async def _notify_scheduled_failure(
        self,
        *,
        data: dict[str, Any],
        error_message: str,
    ) -> None:
        channel = str(data.get("channel") or "whatsapp")
        outbox_phone = str(data.get("channel_identity") or data.get("phone_number") or "")
        if not outbox_phone:
            return
        delivery = self._resolve_delivery_service()
        if not delivery:
            return
        try:
            await delivery.deliver_text(
                phone_number=outbox_phone,
                channel=channel,
                text=f"Scheduled transfer failed: {error_message}",
                metadata={"source": "transfer_executor", "scheduled": True},
                dedupe_key=f"scheduled-failed:{data.get('transaction_id')}",
            )
        except Exception as exc:
            logger.warning("scheduled_failure_notification_failed", error=str(exc))

    async def _enqueue_scheduled_receipt(self, *, data: dict[str, Any], transfer_data: dict[str, Any]) -> None:
        if not self.publisher:
            return

        recipient_data = transfer_data.get("recipient", {}) if isinstance(transfer_data.get("recipient"), dict) else {}
        source_data = transfer_data.get("source", {}) if isinstance(transfer_data.get("source"), dict) else {}
        payload = {
            "phone_number": data.get("phone_number"),
            "channel": data.get("channel", "whatsapp"),
            "channel_identity": data.get("channel_identity"),
            "transfer_data": {
                "amount": transfer_data.get("amount"),
                "source": {
                    "name": source_data.get("bank_name") or source_data.get("name"),
                    "account_name": source_data.get("account_name"),
                    "account_number": source_data.get("account_number"),
                },
                "recipient": {
                    "name": recipient_data.get("name"),
                    "account_number": recipient_data.get("account_number"),
                    "bank_name": recipient_data.get("bank_name"),
                },
                "narration": transfer_data.get("narration"),
                "channel": data.get("channel", "whatsapp"),
                "session_id": data.get("idempotency_key") or data.get("transaction_id"),
                "processor_name": transfer_data.get("processor_name"),
            },
            "transaction_reference": data.get("transaction_id"),
            "signal_key": f"scheduled:receipt:{data.get('transaction_id')}",
        }
        await self.publisher.publish(topic="receipt.process", message=payload)

    async def handle_transfer(self, data: dict[str, Any]) -> None:
        """Handle execution of a transfer transaction."""
        transaction_id = data.get("transaction_id")
        transfer_data = data.get("transfer_data", {})
        locale = data.get("language", "en")
        scheduled_meta = self._scheduled_meta(data)
        schedule_run_id = str(scheduled_meta.get("schedule_run_id")) if scheduled_meta.get("schedule_run_id") else None
        attempt = int(scheduled_meta.get("attempt") or 1)
        is_scheduled = str(scheduled_meta.get("run_source") or "") == "scheduled"

        if not transaction_id:
            logger.error("transfer_execution_error", error="missing_transaction_id")
            return

        logger.info("executing_transfer", transaction_id=transaction_id)

        try:
            if schedule_run_id:
                await self._update_scheduled_run(schedule_run_id, status="processing")

            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = transfer_data.get("amount")
            recipient = transfer_data.get("recipient", {})
            source = transfer_data.get("source", {})
            narration = transfer_data.get("narration")
            source_account_id = source.get("account_id")
            recipient_account = recipient.get("account_number")
            recipient_bank_code = recipient.get("bank_code")
            reference = str(data.get("idempotency_key") or transaction_id)

            if not source_account_id:
                raise ValueError("missing_source_account_id")
            if not recipient_account or not recipient_bank_code:
                raise ValueError("missing_recipient_account_details")
            if not amount or float(amount) <= 0:
                raise ValueError("invalid_transfer_amount")
            amount_value = float(amount)

            source_account = await self.account_repo.get_by_id(str(source_account_id))
            if not source_account or not source_account.mandate_id:
                raise ValueError("source_account_mandate_not_ready")

            result = await self.direct_debit_provider.initiate_debit_to_beneficiary(
                amount=amount_value,
                mandate_id=source_account.mandate_id,
                reference=reference,
                beneficiary_account=str(recipient_account),
                beneficiary_bank_code=str(recipient_bank_code),
                narration=str(narration or "Transfer"),
            )

            if result.success and result.status == DebitStatus.SUCCESSFUL:
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.SUCCESSFUL.value,
                )
                if schedule_run_id:
                    await self._update_scheduled_run(
                        schedule_run_id,
                        status="successful",
                        transaction_id=transaction_id,
                    )
                if is_scheduled:
                    await self._enqueue_scheduled_receipt(data=data, transfer_data=transfer_data)
                logger.info("transfer_success", transaction_id=transaction_id, ref=result.reference)
            elif result.success and result.status in (DebitStatus.PENDING, DebitStatus.PROCESSING):
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.PROCESSING.value,
                )
                logger.info("transfer_processing", transaction_id=transaction_id, ref=result.reference)
            else:
                error_msg = result.error_message or render_message("transfer.error.provider_failed", locale)
                await self.transaction_repo.update_status(
                    transaction_id,
                    TransactionStatusEnum.FAILED.value,
                    error_message=error_msg,
                )
                if schedule_run_id:
                    await self._update_scheduled_run(
                        schedule_run_id,
                        status="failed",
                        transaction_id=transaction_id,
                        error_message=error_msg,
                    )

                if is_scheduled and attempt < 2 and self.publisher:
                    retry_payload = dict(data)
                    retry_meta = dict(scheduled_meta)
                    retry_meta["attempt"] = attempt + 1
                    retry_payload["scheduled_meta"] = retry_meta
                    retry_payload["idempotency_key"] = f"{data.get('idempotency_key')}::retry{attempt + 1}"
                    await self.publisher.publish(topic="transaction.execute", message=retry_payload)
                    logger.info("scheduled_transfer_retry_enqueued", transaction_id=transaction_id, attempt=attempt + 1)
                elif is_scheduled:
                    await self._notify_scheduled_failure(data=data, error_message=error_msg)
                logger.error("transfer_failed", transaction_id=transaction_id, error=error_msg)

        except Exception as e:
            logger.error("transfer_execution_exception", transaction_id=transaction_id, error=str(e))
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=str(e)
            )
            if schedule_run_id:
                await self._update_scheduled_run(
                    schedule_run_id,
                    status="failed",
                    transaction_id=transaction_id,
                    error_message=str(e),
                )
            if is_scheduled:
                await self._notify_scheduled_failure(data=data, error_message=str(e))
