"""Airtime purchase service for handling airtime purchase operations."""

import asyncio
from typing import Dict, Any, Optional

from shared.clients.whatsapp_client import WhatsAppClient
from shared.cache.redis_client import RedisClient
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from apps.core.src.agent.beneficiary.suggestion_service import BeneficiarySuggestionService


class AirtimeCompletionService:
    """Service for airtime purchase operations."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client=None,
        beneficiary_repository: Optional[BeneficiaryRepository] = None,
        beneficiary_suggestion_service: Optional[BeneficiarySuggestionService] = None,
    ):
        """
        Initialize airtime service.

        Args:
            whatsapp_client: WhatsApp client for sending notifications
            redis_client: Redis client for cleanup operations
            beneficiary_repository: Beneficiary repository for checking existing beneficiaries
            beneficiary_suggestion_service: Optional shared service for beneficiary suggestions
        """
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client or RedisClient.get_client()
        self.beneficiary_repository = beneficiary_repository
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

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
            
            # Send success notification first (await to ensure it's sent before beneficiary suggestion)
            await self.whatsapp_client.send_text(to=phone_number, text=message)
            
            print(f"✅ Success notification sent for {phone_number}")
            
            # Check and suggest saving recipient as beneficiary if service is available
            # This runs AFTER the success notification is sent
            if self.beneficiary_suggestion_service:
                recipient = airtime_data.get("recipient", {})
                recipient_phone = recipient.get("phone", "")
                network = recipient.get("network", "")
                
                print(f"DEBUG beneficiary_suggestion: service available, recipient={recipient}, phone={recipient_phone}, network={network}")
                
                # Validate recipient has required fields before calling suggestion service
                if recipient_phone and network:
                    try:
                        await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                            phone_number=phone_number,
                            beneficiary_type="airtime",
                            recipient_data=recipient,
                            transaction_id=transaction_id,
                        )
                        print(f"✅ Beneficiary suggestion call completed for {phone_number}")
                    except Exception as e:
                        # Log specific error but don't break the success notification flow
                        print(f"⚠️  Error in beneficiary suggestion for {phone_number}: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"⚠️  Skipping beneficiary suggestion: missing required fields (phone={recipient_phone}, network={network})")
            else:
                print(f"⚠️  Beneficiary suggestion service not available for {phone_number}")
        except Exception as e:
            print(f"⚠️  Error sending success notification: {e}")
            import traceback
            traceback.print_exc()

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


