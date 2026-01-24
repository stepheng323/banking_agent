"""Airtime Executor.

Handles execution of airtime transactions from the queue.
"""

from typing import Any

from shared.clients.abstractions.banking import BankingDataProvider
from shared.database.enums import TransactionStatusEnum
from shared.repositories.transaction_repository import TransactionRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AirtimeExecutor:
    """Executor for Airtime transactions."""

    def __init__(
        self,
        banking_provider: BankingDataProvider,
        transaction_repo: TransactionRepository,
    ):
        self.banking_provider = banking_provider
        self.transaction_repo = transaction_repo

    async def handle_airtime(self, data: dict[str, Any]) -> None:
        """Handle execution of an airtime transaction."""
        transaction_id = data.get("transaction_id")
        airtime_data = data.get("airtime_data", {})

        if not transaction_id:
            logger.error("airtime_execution_error", error="Missing transaction_id")
            return

        logger.info("executing_airtime", transaction_id=transaction_id)

        try:
            await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)

            amount = airtime_data.get("amount")
            phone_number = airtime_data.get("phone_number")
            network = airtime_data.get("network")

            result = await self.banking_provider.buy_airtime(
                amount=amount,
                phone_number=phone_number,
                network=network,
            )

            if result.get("status") == "success":
                await self.transaction_repo.update_status(transaction_id, TransactionStatusEnum.SUCCESSFUL.value)
                logger.info("airtime_success", transaction_id=transaction_id, ref=result.get("reference"))
            else:
                error_msg = result.get("message", "Airtime purchase failed at provider")
                await self.transaction_repo.update_status(
                    transaction_id, TransactionStatusEnum.FAILED.value, error_message=error_msg
                )
                logger.error("airtime_failed", transaction_id=transaction_id, error=error_msg)

        except Exception as e:
            logger.error("airtime_execution_exception", transaction_id=transaction_id, error=str(e))
            await self.transaction_repo.update_status(
                transaction_id, TransactionStatusEnum.FAILED.value, error_message=str(e)
            )
