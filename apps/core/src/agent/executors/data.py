"""Data Executor.

Handles execution of data transactions from the queue.
"""

from typing import Any

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.database.enums import TransactionStatusEnum
from shared.i18n import render_message
from shared.repositories.transaction_repository import TransactionRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataExecutor:
    """Executor for Data transactions."""

    def __init__(
        self,
        bill_provider: BillPaymentProvider,
        transaction_repo: TransactionRepository,
    ):
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo

    async def handle_data(self, data: dict[str, Any]) -> None:
        """Handle execution of a data transaction."""
        transaction_id = data.get("transaction_id")
        data_details = data.get("data_details", {})
        locale = data.get("language", "en")

        if not transaction_id:
            logger.error("data_execution_error", error="missing_transaction_id")
            return

        logger.info("executing_data", transaction_id=transaction_id)

        try:
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = data_details.get("amount")
            phone_number = data_details.get("phone_number")
            network = data_details.get("network")
            plan_code = data_details.get("plan_code")

            result = await self.bill_provider.buy_data(
                phone_number=phone_number, amount=amount, network=network, plan_code=plan_code
            )

            if result.get("status") == "success":
                await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.SUCCESSFUL.value)
                logger.info("data_success", transaction_id=transaction_id, ref=result.get("reference"))
            else:
                error_msg = result.get("message") or render_message("data.error.provider_failed", locale)
                await self.transaction_repo.update_status(
                    transaction_id, TransactionStatusEnum.FAILED.value, error_message=error_msg
                )
                logger.error("data_failed", transaction_id=transaction_id, error=error_msg)

        except Exception as e:
            logger.error("data_execution_exception", transaction_id=transaction_id, error=str(e))
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=str(e)
            )
