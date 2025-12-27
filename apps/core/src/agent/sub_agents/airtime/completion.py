"""Airtime purchase service for handling airtime purchase operations."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from apps.core.src.agent.tools.beneficiary.suggestion_service import BeneficiarySuggestionService
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.utils.async_helpers import create_background_task
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AirtimeCompletionService:
    """Service for airtime purchase operations."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client=None,
        beneficiary_repository: BeneficiaryRepository | None = None,
        actionable_message_repo: ActionableMessageRepository | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionService | None = None,
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
        self.actionable_message_repo = actionable_message_repo
        self.beneficiary_suggestion_service = beneficiary_suggestion_service

    async def cleanup_redis_keys(self, phone_number: str, idempotency_key: str) -> None:
        """Clean up Redis keys related to the airtime purchase."""
        try:
            await self.redis_client.delete(f"user:{phone_number}:pending_airtime")
            await self.redis_client.delete(f"user:{phone_number}:pending_airtime_flow_token")
            await self.redis_client.delete(f"transaction:token:{idempotency_key}:phone")
            await self.redis_client.delete(f"transaction:retry:{idempotency_key}")
            logger.info("airtime_redis_cleanup", phone=phone_number, idem_key=idempotency_key)
        except Exception as e:
            logger.error(
                "airtime_redis_cleanup_error",
                phone=phone_number,
                idem_key=idempotency_key,
                error=str(e),
                exc_info=True,
            )

    async def send_success_notification(
        self,
        phone_number: str,
        airtime_data: dict[str, Any],
        purchase_result: dict[str, Any],
        transaction_id: str | None = None,
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
                f"✓ Airtime purchase successful!\n\n"
                f"Amount: ₦{amount:,.0f}\n"
                f"Recipient: {recipient_name} ({network})\n"
                f"Phone: {recipient_phone}\n"
                f"Transaction ID: {provider_txn_id}"
            )

            whatapp_res = await self.whatsapp_client.send_text(to=phone_number, text=message)
            wa_message_id = whatapp_res.get("messages", [{}])[0].get("id", "")

            if self.actionable_message_repo and wa_message_id:
                user_id = airtime_data.get("user_id")
                if user_id:
                    self.actionable_message_repo.create(
                        user_id=user_id,
                        wa_message_id=wa_message_id,
                        message_type="airtime_success",
                        message_data=airtime_data,
                        expires_at=datetime.utcnow() + timedelta(days=90),
                    )

            if self.beneficiary_suggestion_service:
                recipient = airtime_data.get("recipient", {})
                recipient_phone = recipient.get("phone", "")
                network = recipient.get("network", "")

                if recipient_phone and network:
                    try:
                        await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                            phone_number=phone_number,
                            beneficiary_type="airtime",
                            recipient_data=recipient,
                            transaction_id=transaction_id,
                        )
                        logger.debug("beneficiary_suggestion_completed", phone=phone_number)
                    except Exception as e:
                        logger.warning(
                            "beneficiary_suggestion_error",
                            phone=phone_number,
                            error=str(e),
                            exc_info=True,
                        )
                else:
                    logger.warning(
                        "beneficiary_suggestion_skipped",
                        phone=phone_number,
                        reason="missing_fields",
                    )
            else:
                logger.debug("beneficiary_suggestion_unavailable", phone=phone_number)
        except Exception as e:
            logger.error(
                "airtime_success_notification_error",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )

    async def send_pending_notification(
        self,
        phone_number: str,
        airtime_data: dict[str, Any],
        purchase_result: dict[str, Any],
        transaction_id: str | None = None,
    ) -> None:
        """Send notification for pending airtime purchase."""
        await asyncio.sleep(2.5)

        try:
            amount = float(airtime_data.get("amount", 0))
            recipient = airtime_data.get("recipient", {})
            recipient_phone = recipient.get("phone", "")

            message = (
                f"⏳ Your ₦{amount:,.0f} airtime purchase for {recipient_phone} is processing.\n\n"
                "You'll receive confirmation shortly. If you don't receive it within 5 minutes, please contact support."
            )

            await self.whatsapp_client.send_text(to=phone_number, text=message)
            logger.info("airtime_pending_notification_sent", phone=phone_number)
        except Exception as e:
            logger.error("airtime_pending_notification_error", phone=phone_number, error=str(e))

    async def send_failure_notification(self, phone_number: str, error_message: str) -> None:
        """Send failure notification for airtime purchase."""
        try:
            message = f"Airtime purchase failed: {error_message}. Please try again."
            create_background_task(self.whatsapp_client.send_text(to=phone_number, text=message))
        except Exception as e:
            logger.error(
                "airtime_failure_notification_error",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )
