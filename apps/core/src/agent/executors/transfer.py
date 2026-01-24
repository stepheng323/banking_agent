"""Transfer Executor.

Handles execution of transfer transactions from the queue.
"""

from typing import Any

from shared.clients.abstractions.banking import BankingDataProvider
from shared.database.enums import TransactionStatusEnum
from shared.repositories.transaction_repository import TransactionRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferExecutor:
    """Executor for Transfer transactions."""

    def __init__(
        self,
        banking_provider: BankingDataProvider,
        transaction_repo: TransactionRepository,
    ):
        self.banking_provider = banking_provider
        self.transaction_repo = transaction_repo

    async def handle_transfer(self, data: dict[str, Any]) -> None:
        """Handle execution of a transfer transaction."""
        transaction_id = data.get("transaction_id")
        transfer_data = data.get("transfer_data", {})

        if not transaction_id:
            logger.error("transfer_execution_error", error="Missing transaction_id")
            return

        logger.info("executing_transfer", transaction_id=transaction_id)

        try:
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = transfer_data.get("amount")
            recipient = transfer_data.get("recipient", {})
            source = transfer_data.get("source", {})
            narration = transfer_data.get("narration")

            result = await self.banking_provider.transfer_funds(
                amount=amount,
                recipient_account=recipient.get("account_number"),
                recipient_bank=recipient.get("bank_code"),
                source_account=source.get("account_id"),
                narration=narration,
            )

            if result.get("status") == "success":
                await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.SUCCESSFUL.value)
                logger.info("transfer_success", transaction_id=transaction_id, ref=result.get("reference"))
            else:
                error_msg = result.get("message", "Transfer failed at provider")
                await self.transaction_repo.update_status(
                    transaction_id, TransactionStatusEnum.FAILED.value, error_message=error_msg
                )
                logger.error("transfer_failed", transaction_id=transaction_id, error=error_msg)

        except Exception as e:
            logger.error("transfer_execution_exception", transaction_id=transaction_id, error=str(e))
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=str(e)
            )
