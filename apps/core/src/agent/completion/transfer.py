"""Transfer service for handling transfer notifications and cleanup."""

import asyncio
import os
import json
import traceback
from typing import Dict, Any, Optional

import redis.asyncio as redis

from shared.clients.whatsapp_client import WhatsAppClient
from shared.clients.s3_client import S3Client
from shared.repositories import BeneficiaryRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.formatters.receipt import generate_receipt_image
from shared.services.receipt_generator import ReceiptGenerator
from apps.core.src.agent.beneficiary.suggestion_service import BeneficiarySuggestionService


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
        beneficiary_suggestion_service: Optional[BeneficiarySuggestionService] = None,
    ):
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client
        self.beneficiary_repository = beneficiary_repository
        self.receipt_generator = receipt_generator
        self.s3_client = s3_client
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

    async def cleanup_redis_keys(self, phone_number: str, idem_key: str) -> None:
        """Clean up Redis keys related to the transfer."""
        try:
            await self.redis_client.delete(f"user:{phone_number}:pending_transfer")
            await self.redis_client.delete(f"user:{phone_number}:pending_transfer_flow_token")
            await self.redis_client.delete(f"transfer:token:{idem_key}:phone")
            await self.redis_client.delete(f"transfer:retry:{idem_key}")
            await self.redis_client.delete(f"transfer:prev:{phone_number}:{idem_key}")
            print(f"✅ Cleaned up Redis keys for transfer: {idem_key}")
        except Exception as e:
            print(f"⚠️  Error cleaning up Redis keys: {e}")

    async def send_success_notification(
        self,
        phone_number: str,
        transfer_data: Dict[str, Any],
        transfer_result: Dict[str, Any],
        transaction_id: Optional[str] = None,
    ) -> None:
        """Send success notification with receipt image to user."""
        try:
            if not _receipts_enabled():
                provider_txn_id = transfer_result.get("transaction_id", "N/A")
                fallback_amount = float(transfer_data.get("amount", 0))
                recipient_name = transfer_data.get(
                    "recipient", {}).get("name", "recipient")
                message = (
                    f"✅ Transfer successful! ₦{fallback_amount:,.0f} has been sent to "
                    f"{recipient_name}. Transaction ID: {provider_txn_id}"
                )
                asyncio.create_task(self.whatsapp_client.send_text(
                    to=phone_number, text=message))
                # Suggest saving beneficiary if service is available
                if self.beneficiary_suggestion_service:
                    recipient = transfer_data.get("recipient", {})
                    await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                        phone_number=phone_number,
                        beneficiary_type="transfer",
                        recipient_data=recipient,
                        transaction_id=transaction_id,
                    )
                print(
                    f"✅ Success text notification queued (receipts disabled) for {phone_number}")
                return

            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions and uow.accounts:
                        txn = uow.transactions.get_by_id(str(transaction_id))
                        if txn:
                            account = None
                            source_account_id = txn.source_account_id
                            if source_account_id is not None:
                                account = uow.accounts.get_by_id(
                                    str(source_account_id))
                            receipt_url = await generate_receipt_image(
                                txn,
                                account,
                                self.receipt_generator,
                                self.s3_client,
                            )

                            asyncio.create_task(
                                self.whatsapp_client.send_image(
                                    to=phone_number,
                                    image_url=receipt_url,
                                    caption="Transaction Receipt",
                                )
                            )

                            uow.transactions.update(txn, receipt_sent=True)
                            uow.commit()

                # Suggest saving beneficiary if service is available
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
                recipient_name = transfer_data.get(
                    "recipient", {}).get("name", "recipient")
                message = (
                    f"✅ Transfer successful! ₦{fallback_amount:,.0f} has been sent to "
                    f"{recipient_name}. Transaction ID: {provider_txn_id}"
                )
                asyncio.create_task(
                    self.whatsapp_client.send_text(
                        to=phone_number, text=message)
                )

            print(f"✅ Success notification queued for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error sending success notification: {e}")
            traceback.print_exc()


    async def send_failure_notification(
        self, phone_number: str, error_message: str
    ) -> None:
        """Send failure notification to user."""
        try:
            message = f"❌ Transfer failed: {error_message}. Please try again."
            asyncio.create_task(
                self.whatsapp_client.send_text(to=phone_number, text=message)
            )
            print(f"✅ Failure notification queued for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error sending failure notification: {e}")
