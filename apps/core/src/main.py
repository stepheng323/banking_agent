import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apps.core.src.consumer import MessageConsumer
from apps.core.src.services.message_handler import MessageHandler
from apps.core.src.services.onboarding.handler import OnboardingHandler
from apps.core.src.services.onboarding.onboarding_service import OnboardingService
from shared.database.connection import init_db
from shared.clients.whatsapp_client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories.user_repository import UserRepository
from shared.database.connection import get_db_session
import os



def setup_dependencies():

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Initialize dependencies
    whatsapp_client = WhatsAppClient()
    redis_queue = RedisQueue(redis_url=redis_url)
    user_repository = UserRepository(db=get_db_session())
    onboarding_service = OnboardingService(whatsapp_client)
    onboarding_handler = OnboardingHandler(
        whatsapp_client, user_repository, onboarding_service
    )

    # Create message handler with injected dependencies
    message_handler = MessageHandler(
        whatsapp_client=whatsapp_client,
        user_repository=user_repository,
        onboarding_handler=onboarding_handler,
    )

    consumer = MessageConsumer(redis_queue=redis_queue, handler=message_handler)

    return consumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    print("🚀 Starting Core Banking Service...")

    try:
        init_db()
        print("   ✅ Database initialized")
    except Exception as e:
        print(f"   ⚠️  Database initialization warning: {e}")

    consumer = setup_dependencies()
    asyncio.create_task(consumer.start())
    print("   ✅ Consumer started in background")

    yield

    print("\n📴 Shutting down Core Banking Service...")
    consumer.stop()
    await asyncio.sleep(0.5)
    print("   ✅ Consumer stopped")


app = FastAPI(title="Core Banking Service", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "Core Banking Service", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
