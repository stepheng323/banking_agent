import asyncio
from shared.queue.redis_queue import RedisQueue
from shared.models.messages import WhatsAppMessage, ProcessedMessage
from apps.core.src.services.message_handler import MessageHandler


class MessageConsumer:
    def __init__(self, redis_url: str, handler: MessageHandler):
        self.queue = RedisQueue(redis_url)
        self.handler = handler
        self.running = False

    async def process_message(self, message_data: dict) -> ProcessedMessage:
        try:
            msg = WhatsAppMessage(**message_data)
            await self.handler.handle_message(msg)

        except Exception as e:
            print(f"   ❌ Processing failed: {e}")
            raise e

    async def start(self, queue_name: str = "banking:messages"):
        self.running = True
        print(f"🚀 Starting message consumer for queue: {queue_name}")

        await self.queue.connect()
        while self.running:
            try:
                message_data = await self.queue.dequeue_blocking(
                    queue_name=queue_name, timeout=5
                )
                if message_data:
                    await self.process_message(message_data)
                else:
                    pass

            except asyncio.CancelledError:
                print("   Consumer cancelled")
                break
            except Exception as e:
                print(f" ❌ Consumer error: {e}")
                import traceback

                traceback.print_exc()
                await asyncio.sleep(1)

        await self.queue.close()
        print("   👋 Consumer stopped")

    def stop(self):
        self.running = False
