"""Transfer service for handling transfer notifications and cleanup."""

from decimal import Decimal
from typing import Any

import redis.asyncio as redis

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.formatters.transfer import format_transfer_pending_message
from shared.receipts import DebitSource, TransferReceiptData
from shared.repositories import BeneficiaryRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.async_helpers import create_background_task
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferCompletionService:
    """Service for handling transfer notifications, cleanup, and beneficiary suggestions."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client: redis.Redis,
        beneficiary_repository: BeneficiaryRepository,
        actionable_message_repo: ActionableMessageRepository,
        beneficiary_suggestion_service: BeneficiarySuggestionService | None = None,
    ):
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client
        self.beneficiary_repository = beneficiary_repository
        self.actionable_message_repo = actionable_message_repo
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

    async def cleanup_redis_keys(self, phone_number: str, idem_key: str) -> None:
        """Clean up Redis keys related to the transfer."""
        try:
            await self.redis_client.delete(f"user:{phone_number}:pending_transfer")
            await self.redis_client.delete(f"user:{phone_number}:pending_transfer_flow_token")
            await self.redis_client.delete(f"transfer:token:{idem_key}:phone")
            await self.redis_client.delete(f"transfer:retry:{idem_key}")
            await self.redis_client.delete(f"transfer:prev:{phone_number}:{idem_key}")
        except Exception as e:
            logger.error(
                "transfer_redis_cleanup_error",
                phone=phone_number,
                idem_key=idem_key,
                error=str(e),
                exc_info=True,
            )

    def _build_receipt_data(
        self,
        transfer_data: dict[str, Any],
        transfer_result: dict[str, Any],
    ) -> TransferReceiptData:
        """Build TransferReceiptData from transfer data and result."""
        recipient = transfer_data.get("recipient", {})
        source = transfer_data.get("source", {})
        amount = Decimal(str(transfer_data.get("amount", 0)))
        fee = Decimal(str(transfer_result.get("fee", 0)))
        total_debited = amount + fee

        debit_sources: list[DebitSource] = []
        if source:
            debit_sources.append(
                DebitSource(
                    account_number=source.get("account_number", ""),
                    bank_name=source.get("bank_name", ""),
                    amount=amount,
                )
            )

        return TransferReceiptData(
            transaction_reference=transfer_result.get("transaction_id", "N/A"),
            amount=amount,
            total_debited=total_debited,
            fee=fee,
            recipient_name=recipient.get("name", ""),
            recipient_account=recipient.get("account_number", ""),
            recipient_bank=recipient.get("bank_name", ""),
            debit_sources=debit_sources,
            narration=transfer_data.get("narration"),
            sender_name=source.get("account_name") or source.get("name"),
            sender_account=source.get("account_number"),
            sender_bank=source.get("bank_name"),
        )

    async def send_success_notification(
        self,
        phone_number: str,
        transfer_data: dict[str, Any],
        transfer_result: dict[str, Any],
        transaction_id: str | None = None,
    ) -> None:
        """Send success notification with image receipt to user.

        Pushes receipt job to queue for async processing by receipt worker.
        """
        try:
            logger.info(
                "queuing_receipt_job",
                phone=phone_number,
                transaction_id=transfer_result.get("transaction_id"),
            )

            import json
            import uuid

            signal_key = f"receipt:signal:{transaction_id}" if transaction_id else f"receipt:signal:{uuid.uuid4()}"

            receipt_job = {
                "phone_number": phone_number,
                "transfer_data": transfer_data,
                "transfer_result": transfer_result,
                "transaction_id": transaction_id,
                "signal_key": signal_key,
            }
            await self.redis_client.rpush(
                "banking:receipt_jobs",
                json.dumps(receipt_job, default=str),
            )

            logger.info("receipt_job_queued", phone=phone_number, signal_key=signal_key)

            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        txn = uow.transactions.get_by_id(str(transaction_id))
                        if txn:
                            uow.transactions.update(txn, receipt_sent=False)
                            uow.commit()

            try:
                await self.redis_client.blpop(signal_key, timeout=20)
            except Exception as e:
                logger.warning("receipt_signal_wait_error", error=str(e))

            if self.beneficiary_suggestion_service:
                recipient = transfer_data.get("recipient", {})
                await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                    phone_number=phone_number,
                    beneficiary_type="transfer",
                    recipient_data=recipient,
                    transaction_id=transaction_id,
                )

        except Exception as e:
            logger.error(
                "transfer_success_notification_error",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )

    async def send_pending_notification(
        self,
        phone_number: str,
        transfer_data: dict[str, Any],
        transfer_result: dict[str, Any],
        transaction_id: str | None = None,
    ) -> None:
        """Send notification for pending transfer."""
        try:
            amount = float(transfer_data.get("amount", 0))
            recipient = transfer_data.get("recipient", {})
            recipient_name = recipient.get("name", "recipient")

            message = format_transfer_pending_message(
                amount=amount,
                recipient_name=recipient_name,
            )

            await self.whatsapp_client.send_text(to=phone_number, text=message)
        except Exception as e:
            logger.error("transfer_pending_notification_error", phone=phone_number, error=str(e))

    async def send_failure_notification(self, phone_number: str, error_message: str) -> None:
        """Send failure notification to user."""
        try:
            message = f"Transfer failed: {error_message}. Please try again."
            create_background_task(self.whatsapp_client.send_text(to=phone_number, text=message))
        except Exception as e:
            logger.error(
                "transfer_failure_notification_error",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )
