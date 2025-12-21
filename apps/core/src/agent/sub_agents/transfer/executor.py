"""Transfer handler service for executing queued transfers."""

import traceback
from datetime import datetime
from typing import Dict, Any

from shared.clients.payment.factory import PaymentProviderFactory
from shared.repositories.unit_of_work import UnitOfWork
from apps.core.src.agent.sub_agents.transfer.completion import TransferCompletionService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferExecutor:
    """Handles transfer execution and notifications."""

    def __init__(
        self,
        transfer_service: TransferCompletionService,
    ):
        self.transfer_service = transfer_service

    def _get_user_friendly_error(self, error: str) -> str:
        """Convert technical error messages to user-friendly messages."""
        error_lower = error.lower()
        
        if "timeout" in error_lower or "connect" in error_lower:
            return "Service temporarily unavailable. Please try again in a few minutes."
        if "invalid" in error_lower and "account" in error_lower:
            return "Invalid account number. Please check and try again."
        if "insufficient" in error_lower or "balance" in error_lower:
            return "Insufficient balance for this transfer."
        if "limit" in error_lower:
            return "Transfer limit exceeded. Try a smaller amount."
        if "pending" in error_lower:
            return "Your request is still processing. Please wait a moment."
        
        return "Unable to complete transfer. Please try again later."

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
        transaction_id = transfer_request["transaction_id"]

        if not phone_number or not idem_key or not transfer_data:
            logger.error("invalid_transfer_request", phone=phone_number, idem_key=idem_key, has_data=bool(transfer_data))
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

            logger.info("transfer_executed", phone=phone_number, result=transfer_result)

            await self.transfer_service.cleanup_redis_keys(phone_number, idem_key)

            if transfer_result.get("success"):
                tx_status = transfer_result.get("status", "successful").lower()
                
                if tx_status == "pending":
                    if transaction_id:
                        with UnitOfWork() as uow:
                            if uow.transactions:
                                transaction = uow.transactions.get_by_id(str(transaction_id))
                                if transaction:
                                    uow.transactions.update(
                                        transaction, status="pending",
                                        transaction_id=transfer_result.get("transaction_id"),
                                        provider_response=transfer_result,
                                    )
                                    uow.commit()
                    logger.info("transfer_pending", phone=phone_number)
                    await self.transfer_service.send_pending_notification(
                        phone_number, transfer_data, transfer_result, transaction_id
                    )
                else:
                    if transaction_id:
                        with UnitOfWork() as uow:
                            if uow.transactions:
                                transaction = uow.transactions.get_by_id(str(transaction_id))
                                if transaction:
                                    uow.transactions.update(
                                        transaction, status="completed",
                                        transaction_id=transfer_result.get("transaction_id"),
                                        provider_response=transfer_result,
                                        completed_at=datetime.utcnow(),
                                    )
                                    uow.commit()
                    await self.transfer_service.send_success_notification(
                        phone_number, transfer_data, transfer_result, transaction_id
                    )
            else:
                error_msg = transfer_result.get("error", "Unknown error")
                if transaction_id:
                    with UnitOfWork() as uow:
                        if uow.transactions:
                            transaction = uow.transactions.get_by_id(str(transaction_id))
                            if transaction:
                                uow.transactions.update(
                                    transaction, status="failed",
                                    error_message=error_msg,
                                    provider_response=transfer_result,
                                    completed_at=datetime.utcnow(),
                                )
                                uow.commit()
                logger.warning("transfer_failed", phone=phone_number, error=error_msg)
                user_msg = self._get_user_friendly_error(error_msg)
                await self.transfer_service.send_failure_notification(phone_number, user_msg)

        except Exception as e:
            logger.error("transfer_execution_error", phone=phone_number, error=str(e), exc_info=True)
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

            await self.transfer_service.send_failure_notification(
                phone_number, "Transfer failed due to an error. Please try again later."
            )

