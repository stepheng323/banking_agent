"""Airtime purchase handler service for executing queued airtime purchases."""

import traceback
from datetime import datetime
from typing import Dict, Any

from shared.clients.factories.payment import PaymentProviderFactory
from shared.repositories.unit_of_work import UnitOfWork
from apps.core.src.agent.sub_agents.airtime.completion import AirtimeCompletionService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AirtimeExecutor:
    """Handles airtime purchase execution and notifications."""

    def __init__(
        self,
        airtime_service: AirtimeCompletionService,
    ):
        """
        Initialize airtime handler.

        Args:
            airtime_service: AirtimeCompletionService instance for notifications and cleanup
        """
        self.airtime_service = airtime_service

    def _update_transaction_status(
        self,
        transaction_id: str,
        status: str,
        error_message: str | None = None,
        provider_response: Dict[str, Any] | None = None,
        provider_transaction_id: str | None = None,
    ) -> None:
        """Update transaction status in database."""
        if not transaction_id:
            return
        with UnitOfWork() as uow:
            if uow.transactions:
                transaction = uow.transactions.get_by_id(str(transaction_id))
                if transaction:
                    uow.transactions.update(
                        transaction,
                        status=status,
                        error_message=error_message,
                        transaction_id=provider_transaction_id,
                        provider_response=provider_response,
                        completed_at=datetime.utcnow(),
                    )
                    uow.commit()

    def _get_user_friendly_error(self, error: str) -> str:
        """Convert technical error messages to user-friendly messages."""
        error_lower = error.lower()
        
        if "timeout" in error_lower or "connect" in error_lower:
            return "Service temporarily unavailable. Please try again in a few minutes."
        if "invalid" in error_lower and "phone" in error_lower:
            return "Invalid phone number format. Please check and try again."
        if "insufficient" in error_lower or "balance" in error_lower:
            return "Insufficient balance for this purchase. Try a smaller amount."
        if "network" in error_lower and "unsupported" in error_lower:
            return "Unsupported network. Please try MTN, Airtel, Glo, or 9mobile."
        if "pending" in error_lower:
            return "Your request is still processing. Please wait a moment."
        
        return "Unable to complete airtime purchase. Please try again later."

    async def handle_airtime(self, airtime_request: Dict[str, Any]) -> None:
        """
        Execute an airtime purchase request and handle notifications.

        Args:
            airtime_request: Dictionary containing:
                - phone_number: User's phone number
                - idempotency_key: Airtime purchase idempotency key
                - airtime_data: Airtime purchase details (amount, recipient_phone, network, etc.)
                - transaction_id: Optional transaction record ID
        """
        phone_number = airtime_request.get("phone_number")
        idem_key = airtime_request.get("idempotency_key")
        airtime_data = airtime_request.get("airtime_data", {})
        transaction_id = airtime_request.get("transaction_id")


        if not phone_number or not idem_key or not airtime_data:
            logger.error("invalid_airtime_request", phone=phone_number, idem_key=idem_key, has_data=bool(airtime_data))
            return

        if transaction_id:
            with UnitOfWork() as uow:
                if uow.transactions:
                    transaction = uow.transactions.get_by_id(str(transaction_id))
                    if transaction:
                        uow.transactions.update(transaction, status="processing")
                        uow.commit()

        try:
            provider = PaymentProviderFactory.get_bill_payment_provider()
            if not provider:
                raise ValueError("No payment provider available for airtime purchases")

            recipient = airtime_data.get("recipient", {})
            recipient_phone = recipient.get("phone", "")
            network = recipient.get("network", "")
            amount = float(airtime_data.get("amount", 0))

            logger.info("airtime_purchase_starting", 
                       phone=phone_number, 
                       recipient=recipient_phone, 
                       network=network, 
                       amount=amount,
                       provider=provider.provider_name)

            purchase_result = await provider.purchase_airtime(
                amount=amount,
                recipient_phone=recipient_phone,
                network=network,
            )

            logger.info("airtime_purchase_result", phone=phone_number, result=purchase_result)

            await self.airtime_service.cleanup_redis_keys(phone_number, idem_key)

            if purchase_result.get("success"):
                tx_status = purchase_result.get("status", "successful").lower()
                
                if tx_status == "pending":
                    self._update_transaction_status(
                        transaction_id, "pending",
                        provider_response=purchase_result,
                        provider_transaction_id=purchase_result.get("transaction_id")
                    )
                    logger.info("airtime_purchase_pending", phone=phone_number, recipient=recipient_phone)
                    await self.airtime_service.send_pending_notification(
                        phone_number, airtime_data, purchase_result, transaction_id
                    )
                else:
                    self._update_transaction_status(
                        transaction_id, "completed",
                        provider_response=purchase_result,
                        provider_transaction_id=purchase_result.get("transaction_id")
                    )
                    logger.info("airtime_purchase_successful", phone=phone_number, recipient=recipient_phone)
                    await self.airtime_service.send_success_notification(
                        phone_number, airtime_data, purchase_result, transaction_id
                    )
            else:
                error_msg = purchase_result.get("error", "Unknown error")
                self._update_transaction_status(
                    transaction_id, "failed",
                    error_message=error_msg,
                    provider_response=purchase_result
                )
                logger.warning("airtime_purchase_failed", phone=phone_number, error=error_msg)
                user_msg = self._get_user_friendly_error(error_msg)
                await self.airtime_service.send_failure_notification(phone_number, user_msg)

        except NotImplementedError as e:
            logger.warning("airtime_not_implemented", error=str(e), exc_info=True)
            self._update_transaction_status(
                transaction_id, "failed",
                error_message="Airtime purchase service not yet available"
            )
            await self.airtime_service.send_failure_notification(
                phone_number, "Airtime purchase service is not yet available. Please try again later."
            )
        except Exception as e:
            logger.error("airtime_execution_error", phone=phone_number, error=str(e), exc_info=True)
            self._update_transaction_status(
                transaction_id, "failed",
                error_message=str(e)
            )

            await self.airtime_service.send_failure_notification(
                phone_number, "Airtime purchase failed due to an error. Please try again later."
            )

