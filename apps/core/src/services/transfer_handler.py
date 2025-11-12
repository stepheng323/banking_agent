"""Transfer handler service for executing queued transfers."""

import asyncio
import traceback
from typing import Dict, Any

import redis.asyncio as redis

from shared.clients.whatsapp_client import WhatsAppClient
from shared.clients.payment_provider_factory import PaymentProviderFactory


class TransferHandler:
    """Handles transfer execution and notifications."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client: redis.Redis,
    ):
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client

    async def handle_transfer(self, transfer_request: Dict[str, Any]) -> None:
        """
        Execute a transfer request and handle notifications.

        Args:
            transfer_request: Dictionary containing:
                - phone_number: User's phone number
                - idempotency_key: Transfer idempotency key
                - transfer_data: Transfer details (amount, recipient, etc.)
        """
        phone_number = transfer_request.get("phone_number")
        idem_key = transfer_request.get("idempotency_key")
        transfer_data = transfer_request.get("transfer_data", {})

        if not phone_number or not idem_key or not transfer_data:
            print("""❌ Invalid transfer request: missing required fields""")
            return

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

            await self._cleanup_redis_keys(phone_number, idem_key)

            if transfer_result.get("success"):
                await self._send_success_notification(
                    phone_number, transfer_data, transfer_result
                )
            else:
                error_msg = transfer_result.get("error", "Unknown error")
                await self._send_failure_notification(phone_number, error_msg)

        except Exception as e:
            print(f"❌ Transfer execution error: {e}")
            traceback.print_exc()
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
    ) -> None:
        """Send success notification to user."""
        try:
            transaction_id = transfer_result.get("transaction_id", "N/A")
            amount = transfer_data.get("amount", 0)
            recipient_name = transfer_data["recipient"]["name"]
            message = (
                f"✅ Transfer successful! ₦{amount:,.0f} has been sent to "
                f"{recipient_name}. Transaction ID: {transaction_id}"
            )
            asyncio.create_task(
                self.whatsapp_client.send_text(to=phone_number, text=message)
            )
            print(f"✅ Success notification queued for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error sending success notification: {e}")

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
