"""Transfer consumer for processing queued transfer requests."""

import asyncio
import traceback

from apps.core.src.handlers.transfer import TransferHandler
from shared.queue.redis_queue import RedisQueue


class TransferConsumer:
    """Consumer for processing transfer execution requests from queue."""

    def __init__(self,
                 redis_queue: RedisQueue,
                 transfer_handler: TransferHandler):
        self.queue = redis_queue
        self.transfer_handler = transfer_handler
        self.running = False

    async def process_transfer(self, transfer_data: dict) -> None:
        """
        Process a transfer request from the queue.

        Args:
            transfer_data: Transfer request dictionary from queue
        """
        try:
            await self.transfer_handler.handle_transfer(transfer_data)
        except Exception as e:
            print(f"❌ Transfer processing failed: {e}")
            traceback.print_exc()
            # Don't re-raise - allow consumer to continue processing other transfers

    async def start(self, queue_name: str = "banking:transfers"):
        """
        Start consuming transfer requests from the queue.

        Args:
            queue_name: Name of the queue to consume from
        """
        self.running = True
        print(f"🚀 Starting transfer consumer for queue: {queue_name}")

        await self.queue.connect()
        while self.running:
            try:
                transfer_data = await self.queue.dequeue_blocking(
                    queue_name=queue_name, timeout=5
                )
                if transfer_data:
                    await self.process_transfer(transfer_data)
                else:
                    # No message available, continue polling
                    pass

            except asyncio.CancelledError:
                print("   Transfer consumer cancelled")
                break
            except Exception as e:
                print(f" ❌ Transfer consumer error: {e}")
                traceback.print_exc()
                await asyncio.sleep(1)

        await self.queue.close()
        print("   👋 Transfer consumer stopped")

    def stop(self):
        """Stop the transfer consumer."""
        self.running = False
