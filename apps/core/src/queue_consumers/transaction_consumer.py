"""Unified transaction consumer for processing all transaction types."""

import asyncio

from apps.core.src.agent.graphs.airtime.executor import AirtimeExecutor
from apps.core.src.agent.graphs.data.executor import DataExecutor
from apps.core.src.agent.graphs.transfer.executor import TransferExecutor
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionConsumer:
    """Unified consumer for processing all transaction types (transfer, airtime, data)."""

    def __init__(
        self,
        redis_queue: RedisQueue,
        transfer_executor: TransferExecutor,
        airtime_executor: AirtimeExecutor,
        data_executor: DataExecutor | None,
    ):
        """
        Initialize transaction consumer.

        Args:
            redis_queue: Redis queue instance
            transfer_executor: Optional TransferExecutor instance
            airtime_executor: Optional AirtimeExecutor instance
            data_executor: Optional DataExecutor instance
        """
        self.queue = redis_queue
        self.transfer_executor = transfer_executor
        self.airtime_executor = airtime_executor
        self.data_executor = data_executor
        self.running = False

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
                if not self.transfer_executor:
                    logger.error("transfer_executor_not_available")
                    return
                if hasattr(self.transfer_executor, "handle_transfer"):
                    await self.transfer_executor.handle_transfer(transaction_data)
                else:
                    logger.error("transfer_executor_missing_method", method="handle_transfer")

            elif transaction_type == "execute_airtime":
                if not self.airtime_executor:
                    logger.error("airtime_executor_not_available")
                    return
                if hasattr(self.airtime_executor, "handle_airtime"):
                    await self.airtime_executor.handle_airtime(transaction_data)
                else:
                    logger.error("airtime_executor_missing_method", method="handle_airtime")

            elif transaction_type == "execute_data":
                if not self.data_executor:
                    logger.error("data_executor_not_available")
                    return
                if hasattr(self.data_executor, "handle_data"):
                    await self.data_executor.handle_data(transaction_data)
                else:
                    logger.error("data_executor_missing_method", method="handle_data")

            else:
                logger.warning("unknown_transaction_type", transaction_type=transaction_type)

        except Exception as e:
            logger.error(
                "transaction_processing_failed",
                transaction_type=transaction_type,
                error=str(e),
                exc_info=True,
            )

    async def start(self, queue_name: str = "banking:transactions"):
        """
        Start consuming transaction requests from the queue.

        Args:
            queue_name: Name of the queue to consume from
        """
        self.running = True
        logger.info("transaction_consumer_started", queue_name=queue_name)

        await self.queue.connect()

        while self.running:
            try:
                transaction_data = await self.queue.dequeue_blocking(queue_name=queue_name, timeout=5)
                if transaction_data:
                    await self.process_transaction(transaction_data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("transaction_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)

        await self.queue.close()

    def stop(self):
        """Stop the transaction consumer."""
        self.running = False
