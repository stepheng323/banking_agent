"""Airtime purchase service for handling airtime purchase operations."""

import asyncio
from typing import Dict, Any, Optional

from shared.clients.whatsapp_client import WhatsAppClient
from shared.cache.redis_client import RedisClient


class AirtimeService:
    """Service for airtime purchase operations."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client=None,
    ):
        """
        Initialize airtime service.

        Args:
            whatsapp_client: WhatsApp client for sending notifications
            redis_client: Redis client for cleanup operations
        """
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client or RedisClient.get_client()

    async def cleanup_redis_keys(self, phone_number: str, idempotency_key: str) -> None:
        """Clean up Redis keys related to the airtime purchase."""
        try:
            await self.redis_client.delete(f"user:{phone_number}:pending_airtime")
            await self.redis_client.delete(f"user:{phone_number}:pending_airtime_flow_token")
            await self.redis_client.delete(f"transaction:token:{idempotency_key}:phone")
            await self.redis_client.delete(f"transaction:retry:{idempotency_key}")
            print(f"✅ Cleaned up Redis keys for airtime purchase: {idempotency_key}")
        except Exception as e:
            print(f"⚠️  Error cleaning up Redis keys: {e}")

    async def send_success_notification(
        self,
        phone_number: str,
        airtime_data: Dict[str, Any],
        purchase_result: Dict[str, Any],
        transaction_id: Optional[str] = None,
    ) -> None:
        """Send success notification for airtime purchase."""
        try:
            amount = float(airtime_data.get("amount", 0))
            recipient = airtime_data.get("recipient", {})
            recipient_phone = recipient.get("phone", "")
            network = recipient.get("network", "")
            recipient_name = recipient.get("name") or recipient_phone

            provider_txn_id = purchase_result.get("transaction_id", "N/A")
            
            message = (
                f"✅ Airtime purchase successful!\n\n"
                f"Amount: ₦{amount:,.0f}\n"
                f"Recipient: {recipient_name} ({network})\n"
                f"Phone: {recipient_phone}\n"
                f"Transaction ID: {provider_txn_id}"
            )
            
            asyncio.create_task(
                self.whatsapp_client.send_text(to=phone_number, text=message)
            )
            
            print(f"✅ Success notification queued for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error sending success notification: {e}")

    async def send_failure_notification(
        self, phone_number: str, error_message: str
    ) -> None:
        """Send failure notification for airtime purchase."""
        try:
            message = f"❌ Airtime purchase failed: {error_message}. Please try again."
            asyncio.create_task(
                self.whatsapp_client.send_text(to=phone_number, text=message)
            )
            print(f"✅ Failure notification queued for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error sending failure notification: {e}")

