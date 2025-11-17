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


def _receipts_enabled() -> bool:
    """Return True if receipts are enabled via env flag."""
    flag = (os.getenv("RECEIPTS_ENABLED") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


class TransferService:
    """Service for handling transfer notifications, cleanup, and beneficiary suggestions."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client: redis.Redis,
        beneficiary_repository: BeneficiaryRepository,
        receipt_generator: ReceiptGenerator,
        s3_client: S3Client,
    ):
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client
        self.beneficiary_repository = beneficiary_repository
        self.receipt_generator = receipt_generator
        self.s3_client = s3_client

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
                await self.check_and_suggest_beneficiary(phone_number, transfer_data, transaction_id)
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

                await self.check_and_suggest_beneficiary(
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

    async def check_and_suggest_beneficiary(
        self,
        phone_number: str,
        transfer_data: Dict[str, Any],
        transaction_id: Optional[str] = None,
    ) -> None:
        """Check if recipient is new beneficiary and suggest saving."""
        try:
            with UnitOfWork() as uow:
                if not uow.users or not uow.transactions:
                    print(
                        "DEBUG suggest_beneficiary: required repos missing (users or transactions)")
                    return

                user = uow.users.get_by_phone(phone_number)
                if not user:
                    print("DEBUG suggest_beneficiary: user not found")
                    return

                recipient = transfer_data.get("recipient", {})
                account_number = recipient.get("account_number")
                bank_code = recipient.get("bank_code")
                recipient_name = recipient.get("name", "")

                if not account_number or not bank_code:
                    print(
                        f"DEBUG suggest_beneficiary: missing fields account_number={account_number}, bank_code={bank_code}")
                    return

                user_id = str(user.id)
                has_beneficiary_repo = bool(uow.beneficiaries)
                exists_in_beneficiaries = False
                if has_beneficiary_repo:
                    try:
                        exists_in_beneficiaries = self.beneficiary_repository.should_suggest_beneficiary(
                            user_id, account_number, bank_code, beneficiary_type="transfer"
                        )
                    except Exception as e:
                        print(
                            f"DEBUG suggest_beneficiary: error in should_suggest_beneficiary: {e}")
                        exists_in_beneficiaries = False

                print(
                    f"DEBUG suggest_beneficiary: user_id={user_id}, acct={account_number}, bank_code={bank_code}, has_repo={has_beneficiary_repo}, exists_in_beneficiaries={exists_in_beneficiaries}")

                if has_beneficiary_repo and not exists_in_beneficiaries:
                    if transaction_id:
                        transaction = uow.transactions.get_by_id(
                            str(transaction_id))
                        if transaction:
                            uow.transactions.update(
                                transaction, beneficiary_suggested=True
                            )
                            uow.commit()
                            print(
                                f"DEBUG suggest_beneficiary: marked transaction {transaction_id} beneficiary_suggested=True")

                    suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
                    masked_acct = f"…{str(account_number)[-4:]}"
                    recipient_display = recipient_name or masked_acct
                    bank_display = recipient.get("bank_name", "") or bank_code
                    suggestion_context = {
                        "transaction_id": transaction_id,
                        "recipient_name": recipient_name,
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "bank_name": recipient.get("bank_name", ""),
                        "alias_suggested": recipient_name or "",
                    }
                    await self.redis_client.set(
                        suggestion_key,
                        json.dumps(suggestion_context),
                        ex=3600,
                    )

                    message = (
                        f"Would you like to save {recipient_display} ({bank_display} • {masked_acct}) as a beneficiary?\n"
                        f"- Reply 'yes' to save\n"
                        f"- Or send a name (e.g., 'Mum') to save with that alias"
                    )
                    asyncio.create_task(
                        self.whatsapp_client.send_text(
                            to=phone_number, text=message
                        )
                    )
                    print(f"✅ Beneficiary suggestion sent for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error checking/suggesting beneficiary: {e}")
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
