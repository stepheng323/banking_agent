import asyncio
from shared.queue.redis_queue import RedisQueue
from shared.models.messages import WhatsAppMessage, ProcessedMessage
import os


class MessageConsumer:
    def __init__(self, redis_url: str):
        self.queue = RedisQueue(redis_url)
        self.running = False

    async def process_message(self, message_data: dict) -> ProcessedMessage:
        """Process a single message from the queue."""
        try:
            msg = WhatsAppMessage(**message_data)

            print(f"\n🔄 Processing message {msg.message_id}")
            print(f"   From: {msg.from_number}")
            print(f"   Text: {msg.text}")

            # TODO: Implement actual processing logic here
            # - LLM intent classification
            # - Entity extraction
            # - Banking operations
            # - Receipt generation

            response_text = f"Processed: {msg.text}"

            result = ProcessedMessage(
                message_id=msg.message_id,
                from_number=msg.from_number,
                intent="echo",
                entities={},
                response=response_text,
                actions=["acknowledge"],
                success=True,
            )

            print(f"   ✅ Processed successfully")
            print(f"   Response: {response_text}")

            return result

        except Exception as e:
            print(f"   ❌ Processing failed: {e}")
            import traceback

            traceback.print_exc()

            return ProcessedMessage(
                message_id=message_data.get("message_id", "unknown"),
                from_number=message_data.get("from_number", "unknown"),
                success=False,
                error=str(e),
            )

    async def start(self, queue_name: str = "banking:messages"):
        self.running = True
        print(f"🚀 Starting message consumer for queue: {queue_name}")
        print(f"   Redis: {self.queue.redis_url}")

        await self.queue.connect()

        while self.running:
            try:
                message_data = await self.queue.dequeue_blocking(
                    queue_name=queue_name, timeout=5
                )

                if message_data:
                    result = await self.process_message(message_data)

                    if not result.success:
                        print(f"   ⚠️  Message processing failed: {result.error}")
                else:
                    pass

            except asyncio.CancelledError:
                print("   Consumer cancelled")
                break
            except Exception as e:
                print(f"   ❌ Consumer error: {e}")
                import traceback

                traceback.print_exc()
                await asyncio.sleep(1)

        await self.queue.close()
        print("   👋 Consumer stopped")

    def stop(self):
        self.running = False
