"""Message consumer for processing queued messages."""
import asyncio
import traceback
from typing import Any, Dict

from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.handlers.onboarding import OnboardingHandler

from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
from shared.queue.redis_queue import RedisQueue
from shared.repositories.user_repository import UserRepository


class MessageConsumer:
    """Message Consumer Class"""

    def __init__(self, redis_queue: RedisQueue,
                 user_repository: UserRepository,
                 onboarding_handler: OnboardingHandler,
                 orchestrator: OrchestratorAgent,
                 whatsapp_client: WhatsAppClient,

                 ):
        self.queue = redis_queue
        self.user_repository = user_repository
        self.onboarding_handler = onboarding_handler
        self.orchestrator = orchestrator
        self.whatsapp_client = whatsapp_client
        self.running = False

    async def process_message(self, message_data: dict) -> None:
        """Process a message from the queue."""
        try:
            msg = WhatsAppMessage(**message_data)
            await self._handle_message(msg)

        except Exception as e:
            print(f"❌ Processing failed: {e}")
            raise e

    async def _handle_message(self, message: WhatsAppMessage) -> Dict[str, Any] | None:
        """Handle a WhatsApp message."""
        phone_number = message.from_number
        
        # Get user first (synchronous database operation)
        # Then load context, passing the user to avoid duplicate database queries
        user = await asyncio.to_thread(
            self.user_repository.get_by_phone, phone_number
        )
        
        # Load context, passing the user to avoid duplicate queries
        # This also avoids SQLAlchemy session concurrency issues
        await self.orchestrator.context_manager.load_user_context(
            phone_number, user=user
        )

        if user is None or getattr(user, "onboarding_status", None) != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return await self.onboarding_handler.handle_onboarding(message)

        response = await self.orchestrator.invoke(
            phone_number, message.text or "", message.message_id
        )

        # Keep WhatsApp send blocking to preserve message order
        if response and response.strip():
            await self.whatsapp_client.send_text(phone_number, response)
            
            # Pre-warm context for next message (non-blocking background task)
            asyncio.create_task(
                self._warm_context_for_next_message(phone_number)
            )

        return {"status": "success", "response": response}
    
    async def _warm_context_for_next_message(self, phone_number: str) -> None:
        """
        Pre-warm user context cache for the next message.
        
        This is a non-blocking background task that refreshes the context
        after sending a response, improving response time for subsequent messages.
        """
        try:
            # Refresh user context in background
            await self.orchestrator.context_manager.load_user_context(phone_number)
        except Exception as e:
            # Silently fail - this is an optimization, not critical
            print(f"⚠️  Context pre-warming failed for {phone_number}: {e}")

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
