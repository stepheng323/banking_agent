"""Transfer handler service for executing queued transfers."""

import asyncio
import json
import traceback
from datetime import datetime
from typing import Dict, Any, Optional

import redis.asyncio as redis

from shared.clients.whatsapp_client import WhatsAppClient
from shared.clients.payment_provider_factory import PaymentProviderFactory
from shared.clients.s3_client import S3Client
from shared.repositories.unit_of_work import UnitOfWork
from shared.formatters.receipt import generate_receipt_image
from shared.services.receipt_generator import ReceiptGenerator
from apps.core.src.services.beneficiary_suggestion import (
    should_suggest_beneficiary,
    format_beneficiary_suggestion,
)


class TransferHandler:
    """Handles transfer execution and notifications."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client: redis.Redis,
    ):
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client
        self.receipt_generator = ReceiptGenerator()
        self.s3_client = S3Client()

    async def handle_transfer(self, transfer_request: Dict[str, Any]) -> None:
        """
        Execute a transfer request and handle notifications.

        Args:
            transfer_request: Dictionary containing:
                - phone_number: User's phone number
                - idempotency_key: Transfer idempotency key
                - transfer_data: Transfer details (amount, recipient, etc.)
                - transaction_id: Optional transaction record ID
        """
        phone_number = transfer_request.get("phone_number")
        idem_key = transfer_request.get("idempotency_key")
        transfer_data = transfer_request.get("transfer_data", {})
        transaction_id = transfer_request.get("transaction_id")

        if not phone_number or not idem_key or not transfer_data:
            print("""❌ Invalid transfer request: missing required fields""")
            return

        if transaction_id:
            with UnitOfWork() as uow:
                if uow.transactions:
                    transaction = uow.transactions.get_by_id(
                        str(transaction_id))
                    if transaction:
                        uow.transactions.update(
                            transaction, status="processing")
                        uow.commit()

        try:
            provider = PaymentProviderFactory.get_provider_for_service(
                "initiate_transfer"
            )
            if not provider:
                raise ValueError("No payment provider available for transfers")

            transfer_result = await provider.initiate_transfer(
                amount=float(transfer_data.get("amount", 0)),
                recipient_account_number=transfer_data["recipient"]["account_number"],
                recipient_bank_code=transfer_data["recipient"]["bank_code"],
                sender_account_number=transfer_data.get(
                    "source", {}).get("account_number"),
                narration=transfer_data.get("narration"),
                currency="NGN"
            )

            print(f"✅ Transfer executed: {transfer_result}")

            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        transaction = uow.transactions.get_by_id(
                            str(transaction_id))
                        if transaction:
                            if transfer_result.get("success"):
                                uow.transactions.update(
                                    transaction,
                                    status="completed",
                                    transaction_id=transfer_result.get(
                                        "transaction_id"),
                                    provider_response=transfer_result,
                                    completed_at=datetime.utcnow(),
                                )
                            else:
                                error_msg = transfer_result.get(
                                    "error", "Unknown error")
                                uow.transactions.update(
                                    transaction,
                                    status="failed",
                                    error_message=error_msg,
                                    provider_response=transfer_result,
                                    completed_at=datetime.utcnow(),
                                )
                            uow.commit()

            await self._cleanup_redis_keys(phone_number, idem_key)

            if transfer_result.get("success"):
                await self._send_success_notification(
                    phone_number, transfer_data, transfer_result, transaction_id
                )
            else:
                error_msg = transfer_result.get("error", "Unknown error")
                await self._send_failure_notification(phone_number, error_msg)

        except Exception as e:
            print(f"❌ Transfer execution error: {e}")
            traceback.print_exc()
            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        transaction = uow.transactions.get_by_id(
                            str(transaction_id))
                        if transaction:
                            uow.transactions.update(
                                transaction,
                                status="failed",
                                error_message=str(e),
                                completed_at=datetime.utcnow(),
                            )
                            uow.commit()

            await self._send_failure_notification(
                phone_number, "Transfer failed due to an error. Please try again later."
            )

    async def _cleanup_redis_keys(self, phone_number: str, idem_key: str) -> None:
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

    async def _send_success_notification(
        self,
        phone_number: str,
        transfer_data: Dict[str, Any],
        transfer_result: Dict[str, Any],
        transaction_id: Optional[str] = None,
    ) -> None:
        """Send success notification with receipt image to user."""
        try:
            transaction_record = None
            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        transaction_record = uow.transactions.get_by_id(
                            str(transaction_id))

            if transaction_record:
                # Generate receipt image and upload to S3
                with UnitOfWork() as uow:
                    if uow.transactions and uow.accounts:
                        receipt_url = await generate_receipt_image(
                            transaction_record,
                            uow.accounts,
                            self.receipt_generator,
                            self.s3_client,
                        )

                        # Send image via WhatsApp
                        asyncio.create_task(
                            self.whatsapp_client.send_image(
                                to=phone_number,
                                image_url=receipt_url,
                                caption="Transaction Receipt",
                            )
                        )

                        # Mark receipt as sent
                        transaction = uow.transactions.get_by_id(
                            str(transaction_id))
                        if transaction:
                            uow.transactions.update(
                                transaction, receipt_sent=True)
                            uow.commit()

                await self._check_and_suggest_beneficiary(
                    phone_number, transfer_data, transaction_id
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

    async def _check_and_suggest_beneficiary(
        self,
        phone_number: str,
        transfer_data: Dict[str, Any],
        transaction_id: Optional[str] = None,
    ) -> None:
        """Check if recipient is new beneficiary and suggest saving."""
        try:
            with UnitOfWork() as uow:
                if not uow.users or not uow.transactions:
                    return

                user = uow.users.get_by_phone(phone_number)
                if not user:
                    return

                recipient = transfer_data.get("recipient", {})
                account_number = recipient.get("account_number")
                bank_code = recipient.get("bank_code")
                recipient_name = recipient.get("name", "")

                if not account_number or not bank_code:
                    return

                if uow.beneficiaries and should_suggest_beneficiary(
                    str(user.id), account_number, bank_code, uow.beneficiaries
                ):
                    if transaction_id:
                        transaction = uow.transactions.get_by_id(
                            str(transaction_id))
                        if transaction:
                            uow.transactions.update(
                                transaction, beneficiary_suggested=True
                            )
                            uow.commit()

                    suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
                    suggestion_context = {
                        "transaction_id": transaction_id,
                        "recipient_name": recipient_name,
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "bank_name": recipient.get("bank_name", ""),
                    }
                    await self.redis_client.set(
                        suggestion_key,
                        json.dumps(suggestion_context),
                        ex=3600,
                    )

                    suggestion_message = format_beneficiary_suggestion(
                        recipient_name)
                    asyncio.create_task(
                        self.whatsapp_client.send_text(
                            to=phone_number, text=suggestion_message
                        )
                    )
                    print(f"✅ Beneficiary suggestion sent for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error checking/suggesting beneficiary: {e}")
            traceback.print_exc()

    async def _send_failure_notification(
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
