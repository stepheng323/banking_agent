"""Message consumer for processing queued messages."""
import asyncio
import traceback

from apps.core.src.services.message_handler import MessageHandler
from shared.models.messages import WhatsAppMessage
from shared.queue.redis_queue import RedisQueue


class MessageConsumer:
    """Message Consumer Class"""

    def __init__(self, redis_queue: RedisQueue, handler: MessageHandler):
        self.queue = redis_queue
        self.handler = handler
        self.running = False

    async def process_message(self, message_data: dict) -> None:
        """Process a message from the queue."""
        try:
            msg = WhatsAppMessage(**message_data)
            await self.handler.handle_message(msg)

        except Exception as e:
            print(f"❌ Processing failed: {e}")
            raise e

    async def start(self, queue_name: str = "banking:messages"):
        """Start the message consumer."""
        self.running = True
        print(f"🚀 Starting message consumer for queue: {queue_name}")

        await self.queue.connect()
        while self.running:
            try:
                message_data = await self.queue.dequeue_blocking(queue_name=queue_name, timeout=5)
                if message_data:
                    await self.process_message(message_data)
                else:
                    pass

            except asyncio.CancelledError:
                print("   Consumer cancelled")
                break
            except Exception as e:
                print(f" ❌ Consumer error: {e}")

                traceback.print_exc()
                await asyncio.sleep(1)

        await self.queue.close()
        print("   👋 Consumer stopped")

    def stop(self):
        """Stop the message consumer."""
        self.running = False
