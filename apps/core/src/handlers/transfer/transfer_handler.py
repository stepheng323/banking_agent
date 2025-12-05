"""Transfer handler service for executing queued transfers."""

import traceback
from datetime import datetime
from typing import Dict, Any

from shared.clients.payment_provider_factory import PaymentProviderFactory
from shared.repositories.unit_of_work import UnitOfWork
from apps.core.src.agent.completion.transfer import TransferCompletionService


class TransferHandler:
    """Handles transfer execution and notifications."""

    def __init__(
        self,
        transfer_service: TransferCompletionService,
    ):
        self.transfer_service = transfer_service

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
            print("""❌ Invalid transfer request: missing required fields""")
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

            print(f"✅ Transfer executed: {transfer_result}")

            if transaction_id:
                with UnitOfWork() as uow:
                    if uow.transactions:
                        transaction = uow.transactions.get_by_id(
                            str(transaction_id))
                        if transaction:
                            if transfer_result.get("success"):
                                uow.transactions.update(
                                    transaction,
                                    status="completed",
                                    transaction_id=transfer_result.get(
                                        "transaction_id"),
                                    provider_response=transfer_result,
                                    completed_at=datetime.utcnow(),
                                )
                            else:
                                error_msg = transfer_result.get(
                                    "error", "Unknown error")
                                uow.transactions.update(
                                    transaction,
                                    status="failed",
                                    error_message=error_msg,
                                    provider_response=transfer_result,
                                    completed_at=datetime.utcnow(),
                                )
                            uow.commit()

            await self.transfer_service.cleanup_redis_keys(phone_number, idem_key)

            if transfer_result.get("success"):
                await self.transfer_service.send_success_notification(
                    phone_number, transfer_data, transfer_result, transaction_id
                )
            else:
                error_msg = transfer_result.get("error", "Unknown error")
                await self.transfer_service.send_failure_notification(phone_number, error_msg)

        except Exception as e:
            print(f"❌ Transfer execution error: {e}")
            traceback.print_exc()
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
