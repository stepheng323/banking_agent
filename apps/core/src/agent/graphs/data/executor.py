"""Data purchase handler service for executing queued data purchases."""

from datetime import datetime
from typing import Any

from apps.core.src.agent.graphs.data.completion import DataCompletionService
from shared.clients.factories.payment import PaymentProviderFactory
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataExecutor:
    """Handles data purchase execution and notifications."""

    def __init__(
        self,
        data_service: DataCompletionService,
    ):
        """
        Initialize data handler.

        Args:
            data_service: DataCompletionService instance for notifications and cleanup
        """
        self.data_service = data_service

    def _update_transaction_status(
        self,
        transaction_id: str,
        status: str,
        error_message: str | None = None,
        provider_response: dict[str, Any] | None = None,
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
        if "invalid" in error_lower and "code" in error_lower:
            return "Invalid plan code. Please try selecting the plan again."
        if "insufficient" in error_lower or "balance" in error_lower:
            return "Insufficient balance for this purchase."
        if "network" in error_lower and "unsupported" in error_lower:
            return "Unsupported network. Please try MTN, Airtel, Glo, or 9mobile."
        if "pending" in error_lower:
            return "Your request is still processing. Please wait a moment."

        return "Unable to complete data purchase. Please try again later."

    async def handle_data(self, data_request: dict[str, Any]) -> None:
        """
        Execute a data purchase request and handle notifications.

        Args:
            data_request: Dictionary containing:
                - phone_number: User's phone number
                - idempotency_key: Data purchase idempotency key
                - data_purchase: Data purchase details
                - transaction_id: Optional transaction record ID
        """
        phone_number = data_request.get("phone_number")
        idem_key = data_request.get("idempotency_key")
        data_purchase = data_request.get("data_purchase", {})
        transaction_id = data_request.get("transaction_id")

        if not phone_number or not idem_key or not data_purchase:
            logger.error(
                "invalid_data_request",
                phone=phone_number,
                idem_key=idem_key,
                has_data=bool(data_purchase),
            )
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
                raise ValueError("No payment provider available for data purchases")

            plan_code = data_purchase.get("plan_code", "")
            target_phone = data_purchase.get("target_phone", "")
            network = data_purchase.get("network", "")
            amount = float(data_purchase.get("amount", 0))

            logger.info(
                "data_purchase_starting",
                phone=phone_number,
                target_phone=target_phone,
                network=network,
                plan=plan_code,
                amount=amount,
                provider=provider.provider_name,
            )

            purchase_result = await provider.purchase_data(
                plan_code=plan_code,
                recipient_phone=target_phone,
                network=network,
            )

            logger.info("data_purchase_result", phone=phone_number, result=purchase_result)

            await self.data_service.cleanup_redis_keys(phone_number, idem_key)

            if purchase_result.get("success"):
                tx_status = purchase_result.get("status", "successful").lower()

                if tx_status == "pending":
                    self._update_transaction_status(
                        transaction_id,
                        "pending",
                        provider_response=purchase_result,
                        provider_transaction_id=purchase_result.get("transaction_id"),
                    )
                    logger.info("data_purchase_pending", phone=phone_number)
                    await self.data_service.send_pending_notification(
                        phone_number, data_purchase, purchase_result, transaction_id
                    )
                else:
                    self._update_transaction_status(
                        transaction_id,
                        "completed",
                        provider_response=purchase_result,
                        provider_transaction_id=purchase_result.get("transaction_id"),
                    )
                    logger.info("data_purchase_successful", phone=phone_number)
                    await self.data_service.send_success_notification(
                        phone_number, data_purchase, purchase_result, transaction_id
                    )
            else:
                error_msg = purchase_result.get("error", "Unknown error")
                self._update_transaction_status(
                    transaction_id,
                    "failed",
                    error_message=error_msg,
                    provider_response=purchase_result,
                )
                logger.warning("data_purchase_failed", phone=phone_number, error=error_msg)
                user_msg = self._get_user_friendly_error(error_msg)
                await self.data_service.send_failure_notification(phone_number, user_msg)

        except NotImplementedError as e:
            logger.warning("data_not_implemented", error=str(e), exc_info=True)
            self._update_transaction_status(
                transaction_id, "failed", error_message="Data purchase service not yet available"
            )
            await self.data_service.send_failure_notification(
                phone_number,
                "Data purchase service is not yet available. Please try again later.",
            )
        except Exception as e:
            logger.error("data_execution_error", phone=phone_number, error=str(e), exc_info=True)
            self._update_transaction_status(transaction_id, "failed", error_message=str(e))

            await self.data_service.send_failure_notification(
                phone_number, "Data purchase failed due to an error. Please try again later."
            )
