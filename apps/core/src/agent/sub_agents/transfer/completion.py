"""Transfer service for handling transfer notifications and cleanup."""

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

import redis.asyncio as redis

from apps.core.src.agent.tools.beneficiary.suggestion_service import BeneficiarySuggestionService
from shared.clients.storage.s3_client import S3Client
from shared.clients.whatsapp.client import WhatsAppClient
from shared.formatters.receipt import generate_receipt_image
from shared.formatters.transfer import (
    format_transfer_pending_message,
    format_transfer_success_message,
)
from shared.repositories import BeneficiaryRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.receipt_generator import ReceiptGenerator
from shared.utils.async_helpers import create_background_task
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _receipts_enabled() -> bool:
    """Return True if receipts are enabled via env flag."""
    flag = (os.getenv("RECEIPTS_ENABLED") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


class TransferCompletionService:
    """Service for handling transfer notifications, cleanup, and beneficiary suggestions."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client: redis.Redis,
        beneficiary_repository: BeneficiaryRepository,
        receipt_generator: ReceiptGenerator,
        s3_client: S3Client,
        actionable_message_repo: ActionableMessageRepository,
        beneficiary_suggestion_service: BeneficiarySuggestionService | None = None,
    ):
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client
        self.beneficiary_repository = beneficiary_repository
        self.receipt_generator = receipt_generator
        self.s3_client = s3_client
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
            logger.info("transfer_redis_cleanup", phone=phone_number, idem_key=idem_key)
        except Exception as e:
            logger.error(
                "transfer_redis_cleanup_error",
                phone=phone_number,
                idem_key=idem_key,
                error=str(e),
                exc_info=True,
            )

    async def send_success_notification(
        self,
        phone_number: str,
        transfer_data: dict[str, Any],
        transfer_result: dict[str, Any],
        transaction_id: str | None = None,
    ) -> None:
        """Send success notification with receipt image to user."""
        try:
            if not _receipts_enabled():
                provider_txn_id = transfer_result.get("transaction_id", "N/A")
                fallback_amount = float(transfer_data.get("amount", 0))
                recipient_name = transfer_data.get("recipient", {}).get("name", "recipient")
                message = format_transfer_success_message(
                    amount=fallback_amount,
                    recipient_name=recipient_name,
                    transaction_id=provider_txn_id,
                )
                whatapp_res = await self.whatsapp_client.send_text(to=phone_number, text=message)
                wa_message_id = whatapp_res.get("messages", [{}])[0].get("id", "")

                user_id = transfer_data.get("user_id", "")

                def save_actionable_message():
                    with UnitOfWork() as uow:
                        if uow.actionable_messages:
                            uow.actionable_messages.create(
                                user_id=user_id,
                                wa_message_id=wa_message_id,
                                message_type="transfer_success",
                                message_data=transfer_data,
                                expires_at=datetime.utcnow() + timedelta(days=90),
                            )
                            uow.commit()

                await asyncio.to_thread(save_actionable_message)

                if self.beneficiary_suggestion_service:
                    recipient = transfer_data.get("recipient", {})
                    await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                        phone_number=phone_number,
                        beneficiary_type="transfer",
                        recipient_data=recipient,
                        transaction_id=transaction_id,
                    )
                logger.info(
                    "transfer_success_notification_queued",
                    phone=phone_number,
                    receipts_enabled=False,
                )
                return

            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions and uow.accounts:
                        txn = uow.transactions.get_by_id(str(transaction_id))
                        if txn:
                            account = None
                            source_account_id = txn.source_account_id
                            if source_account_id is not None:
                                account = uow.accounts.get_by_id(str(source_account_id))
                            receipt_url = await generate_receipt_image(
                                txn,
                                account,
                                self.receipt_generator,
                                self.s3_client,
                            )

                            await self.whatsapp_client.send_image(
                                to=phone_number,
                                image_url=receipt_url,
                                caption="Transaction Receipt",
                            )

                            uow.transactions.update(txn, receipt_sent=True)
                            uow.commit()

                if self.beneficiary_suggestion_service:
                    recipient = transfer_data.get("recipient", {})
                    await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                        phone_number=phone_number,
                        beneficiary_type="transfer",
                        recipient_data=recipient,
                        transaction_id=transaction_id,
                    )
            else:
                provider_txn_id = transfer_result.get("transaction_id", "N/A")
                fallback_amount = float(transfer_data.get("amount", 0))
                recipient_name = transfer_data.get("recipient", {}).get("name", "recipient")
                message = format_transfer_success_message(
                    amount=fallback_amount,
                    recipient_name=recipient_name,
                    transaction_id=provider_txn_id,
                )
                await self.whatsapp_client.send_text(to=phone_number, text=message)

            logger.info(
                "transfer_success_notification_queued",
                phone=phone_number,
                has_receipt=bool(transaction_id),
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
            logger.info("transfer_pending_notification_sent", phone=phone_number)
        except Exception as e:
            logger.error("transfer_pending_notification_error", phone=phone_number, error=str(e))

    async def send_failure_notification(self, phone_number: str, error_message: str) -> None:
        """Send failure notification to user."""
        try:
            message = f"Transfer failed: {error_message}. Please try again."
            create_background_task(self.whatsapp_client.send_text(to=phone_number, text=message))
            logger.info(
                "transfer_failure_notification_queued", phone=phone_number, error=error_message
            )
        except Exception as e:
            logger.error(
                "transfer_failure_notification_error",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )
