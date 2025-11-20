"""Unified transaction consumer for processing all transaction types."""

import asyncio
import traceback
from typing import Optional, Any

from shared.queue.redis_queue import RedisQueue


class TransactionConsumer:
    """Unified consumer for processing all transaction types (transfer, airtime, data)."""

    def __init__(
        self,
        redis_queue: RedisQueue,
        transfer_handler: Optional[Any] = None,
        airtime_handler: Optional[Any] = None,
        data_handler: Optional[Any] = None,
    ):
        """
        Initialize transaction consumer.

        Args:
            redis_queue: Redis queue instance
            transfer_handler: Optional TransferHandler instance
            airtime_handler: Optional AirtimeHandler instance
            data_handler: Optional DataHandler instance (for future use)
        """
        self.queue = redis_queue
        self.transfer_handler = transfer_handler
        self.airtime_handler = airtime_handler
        self.data_handler = data_handler
        self.running = False

    async def process_transaction(self, transaction_data: dict) -> None:
        """
        Process a transaction request from the queue.

        Routes to appropriate handler based on transaction type.

        Args:
            transaction_data: Transaction request dictionary from queue
        """
        transaction_type = transaction_data.get("type")
        
        if not transaction_type:
            print("❌ Invalid transaction request: missing 'type' field")
            return

        try:
            if transaction_type == "execute_transfer":
                if not self.transfer_handler:
                    print("❌ Transfer handler not available")
                    return
                if hasattr(self.transfer_handler, "handle_transfer"):
                    await self.transfer_handler.handle_transfer(transaction_data)
                else:
                    print(f"❌ Transfer handler missing handle_transfer method")
            
            elif transaction_type == "execute_airtime":
                if not self.airtime_handler:
                    print("❌ Airtime handler not available")
                    return
                if hasattr(self.airtime_handler, "handle_airtime"):
                    await self.airtime_handler.handle_airtime(transaction_data)
                else:
                    print(f"❌ Airtime handler missing handle_airtime method")
            
            elif transaction_type == "execute_data":
                if not self.data_handler:
                    print("❌ Data handler not available")
                    return
                if hasattr(self.data_handler, "handle_data"):
                    await self.data_handler.handle_data(transaction_data)
                else:
                    print(f"❌ Data handler missing handle_data method")
            
            else:
                print(f"❌ Unknown transaction type: {transaction_type}")
        
        except Exception as e:
            print(f"❌ Transaction processing failed for type {transaction_type}: {e}")
            traceback.print_exc()
            # Don't re-raise - allow consumer to continue processing other transactions

    async def start(self, queue_name: str = "banking:transactions"):
        """
        Start consuming transaction requests from the queue.

        Args:
            queue_name: Name of the queue to consume from
        """
        self.running = True
        print(f"🚀 Starting unified transaction consumer for queue: {queue_name}")

        await self.queue.connect()
        
        while self.running:
            try:
                transaction_data = await self.queue.dequeue_blocking(
                    queue_name=queue_name, timeout=5
                )
                if transaction_data:
                    await self.process_transaction(transaction_data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Transaction consumer error: {e}")
                traceback.print_exc()
                await asyncio.sleep(1)

        await self.queue.close()

    def stop(self):
        """Stop the transaction consumer."""
        self.running = False

