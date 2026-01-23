"""Data purchase service for handling data purchase operations."""

import asyncio
from typing import Any

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataCompletionService:
    """Service for data purchase operations."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client=None,
        beneficiary_repository: BeneficiaryRepository | None = None,
        actionable_message_repo: ActionableMessageRepository | None = None,
        beneficiary_suggestion_service: BeneficiarySuggestionService | None = None,
    ):
        """
        Initialize data service.

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
        """Clean up Redis keys related to the data purchase."""
        try:
            await self.redis_client.delete(f"user:{phone_number}:pending_data")
            await self.redis_client.delete(f"transaction:token:{idempotency_key}:phone")
            await self.redis_client.delete(f"transaction:retry:{idempotency_key}")
            logger.info("data_redis_cleanup", phone=phone_number, idem_key=idempotency_key)
        except Exception as e:
            logger.error(
                "data_redis_cleanup_error",
                phone=phone_number,
                idem_key=idempotency_key,
                error=str(e),
                exc_info=True,
            )

    async def send_success_notification(
        self,
        phone_number: str,
        data_purchase: dict[str, Any],
        purchase_result: dict[str, Any],
        transaction_id: str | None = None,
    ) -> None:
        """Send success notification for data purchase."""
        try:
            amount = float(data_purchase.get("amount", 0))
            recipient = data_purchase.get("recipient", {})
            recipient_phone = recipient.get("phone", "")
            network = recipient.get("network", "")
            plan_name = data_purchase.get("plan_name", "Data Plan")
            recipient_name = recipient.get("name") or recipient_phone

            provider_txn_id = purchase_result.get("transaction_id", "N/A")

            message = (
                f"✓ Data purchase successful!\n\n"
                f"Plan: {plan_name}\n"
                f"Amount: ₦{amount:,.0f}\n"
                f"Recipient: {recipient_name} ({network})\n"
                f"Phone: {recipient_phone}\n"
                f"Transaction ID: {provider_txn_id}"
            )

            # Removed direct send_text.
            logger.info("data_success_msg_ready", msg=message)
            # Actionable message saving disabled in headless mode
            # if self.actionable_message_repo and wa_message_id: ...

            if self.beneficiary_suggestion_service:
                recipient = data_purchase.get("recipient", {})
                recipient_phone = recipient.get("phone", "")
                network = recipient.get("network", "")

                if recipient_phone and network:
                    try:
                        await self.beneficiary_suggestion_service.check_and_suggest_beneficiary(
                            phone_number=phone_number,
                            beneficiary_type="data",
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
                "data_success_notification_error",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )

    async def send_pending_notification(
        self,
        phone_number: str,
        data_purchase: dict[str, Any],
        purchase_result: dict[str, Any],
        transaction_id: str | None = None,
    ) -> None:
        """Send notification for pending data purchase."""
        await asyncio.sleep(2.5)

        try:
            amount = float(data_purchase.get("amount", 0))
            recipient = data_purchase.get("recipient", {})
            recipient_phone = recipient.get("phone", "")
            plan_name = data_purchase.get("plan_name", "Data Plan")

            message = (
                f"⏳ Your {plan_name} purchase (₦{amount:,.0f}) for {recipient_phone} is processing.\n\n"
                "You'll receive confirmation shortly. If you don't receive it within 5 minutes, please contact support."
            )

            # Removed direct send_text.
            logger.info("data_pending_notification_sent_log", phone=phone_number)
        except Exception as e:
            logger.error("data_pending_notification_error", phone=phone_number, error=str(e))

    async def send_failure_notification(self, phone_number: str, error_message: str) -> None:
        """Send failure notification for data purchase."""
        try:
            message = f"Data purchase failed: {error_message}. Please try again."
            # Removed direct send_text.
            logger.info("data_failure_notification_log", phone=phone_number, error=error_message)
        except Exception as e:
            logger.error(
                "data_failure_notification_error",
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )
