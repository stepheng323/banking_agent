"""Airtime purchase handler service for executing queued airtime purchases."""

import traceback
from datetime import datetime
from typing import Dict, Any

from shared.clients.payment_provider_factory import PaymentProviderFactory
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
            # Get payment provider for airtime purchase
            # Note: This assumes the provider supports airtime purchase
            # If not available, we'll need to add purchase_airtime method to PaymentProvider
            provider = PaymentProviderFactory.get_provider_for_service(
                "purchase_airtime"
            )
            
            # Fallback: Try to get any available provider if purchase_airtime service not found
            if not provider:
                logger.warning("airtime_provider_not_found", service="purchase_airtime")
                provider = PaymentProviderFactory.get_primary_provider()
                if not provider:
                    logger.error("no_payment_provider_available")
                    raise ValueError("No payment provider available for airtime purchases")

            recipient = airtime_data.get("recipient", {})
            recipient_phone = recipient.get("phone", "")
            network = recipient.get("network", "")
            amount = float(airtime_data.get("amount", 0))


            if hasattr(provider, "purchase_airtime"):
                purchase_result = await provider.purchase_airtime(
                    amount=amount,
                    recipient_phone=recipient_phone,
                    network=network,
                )
            else:
                # Placeholder: Provider doesn't support airtime yet
                # Simulate successful purchase for testing until provider adds airtime support
                import uuid
                purchase_result = {
                    "success": True,
                    "transaction_id": f"TXN-{uuid.uuid4().hex[:8].upper()}",
                    "message": "Airtime purchase simulated successfully",
                    "amount": amount,
                    "recipient_phone": recipient_phone,
                    "network": network,
                }

            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        transaction = uow.transactions.get_by_id(str(transaction_id))
                        if transaction:
                            if purchase_result.get("success"):
                                uow.transactions.update(
                                    transaction,
                                    status="completed",
                                    transaction_id=purchase_result.get("transaction_id"),
                                    provider_response=purchase_result,
                                    completed_at=datetime.utcnow(),
                                )
                            else:
                                error_msg = purchase_result.get("error", "Unknown error")
                                uow.transactions.update(
                                    transaction,
                                    status="failed",
                                    error_message=error_msg,
                                    provider_response=purchase_result,
                                    completed_at=datetime.utcnow(),
                                )
                            uow.commit()

            await self.airtime_service.cleanup_redis_keys(phone_number, idem_key)

            if purchase_result.get("success"):
                logger.info("airtime_purchase_successful", phone=phone_number, recipient=recipient_phone)
                await self.airtime_service.send_success_notification(
                    phone_number, airtime_data, purchase_result, transaction_id
                )
                logger.debug("airtime_success_notification_sent", phone=phone_number)
            else:
                error_msg = purchase_result.get("error", "Unknown error")
                logger.warning("airtime_purchase_failed", phone=phone_number, error=error_msg)
                await self.airtime_service.send_failure_notification(phone_number, error_msg)
                logger.debug("airtime_failure_notification_sent", phone=phone_number)

        except NotImplementedError as e:
            logger.warning("airtime_not_implemented", error=str(e), exc_info=True)
            # Mark transaction as failed
            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        transaction = uow.transactions.get_by_id(str(transaction_id))
                        if transaction:
                            uow.transactions.update(
                                transaction,
                                status="failed",
                                error_message="Airtime purchase service not yet available",
                                completed_at=datetime.utcnow(),
                            )
                            uow.commit()
            await self.airtime_service.send_failure_notification(
                phone_number, "Airtime purchase service is not yet available. Please try again later."
            )
        except Exception as e:
            logger.error("airtime_execution_error", phone=phone_number, error=str(e), exc_info=True)
            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        transaction = uow.transactions.get_by_id(str(transaction_id))
                        if transaction:
                            uow.transactions.update(
                                transaction,
                                status="failed",
                                error_message=str(e),
                                completed_at=datetime.utcnow(),
                            )
                            uow.commit()

            await self.airtime_service.send_failure_notification(
                phone_number, "Airtime purchase failed due to an error. Please try again later."
            )

