"""Unified transaction consumer for processing all transaction types."""

from banking.transactions.runtime.protocols import (
    AirtimeExecutorProtocol,
    DataExecutorProtocol,
    TransferExecutorProtocol,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionConsumer:
    """Unified consumer for processing all transaction types (transfer, airtime, data)."""

    def __init__(
        self,
        transfer_executor: TransferExecutorProtocol | None = None,
        airtime_executor: AirtimeExecutorProtocol | None = None,
        data_executor: DataExecutorProtocol | None = None,
    ):
        """
        Initialize transaction consumer.

        Args:
            transfer_executor: Optional TransferExecutor instance
            airtime_executor: Optional AirtimeExecutor instance
            data_executor: Optional DataExecutor instance
        """
        self.transfer_executor = transfer_executor
        self.airtime_executor = airtime_executor
        self.data_executor = data_executor

    async def process_transaction(self, transaction_data: dict) -> None:
        """
        Process a transaction request from the queue.

        Routes to appropriate handler based on transaction type.

        Args:
            transaction_data: Transaction request dictionary from queue
        """
        if not transaction_data:
            return

        logger.info("transaction_consumer_received", type=transaction_data.get("type"))
        transaction_type = transaction_data.get("type")

        if not transaction_type:
            logger.error("invalid_transaction_request", error="missing type field")
            return

        try:
            if transaction_type == "execute_transfer":
                if self.transfer_executor:
                    await self.transfer_executor.handle_transfer(transaction_data)
                else:
                    logger.error("transfer_executor_not_available")

            elif transaction_type == "execute_airtime":
                if self.airtime_executor:
                    await self.airtime_executor.handle_airtime(transaction_data)
                else:
                    logger.error("airtime_executor_not_available")

            elif transaction_type == "execute_data":
                if self.data_executor:
                    await self.data_executor.handle_data(transaction_data)
                else:
                    logger.error("data_executor_not_available")

            else:
                logger.warning("unknown_transaction_type", transaction_type=transaction_type)

        except Exception as e:
            logger.error(
                "transaction_processing_failed",
                transaction_type=transaction_type,
                error=str(e),
                exc_info=True,
            )
