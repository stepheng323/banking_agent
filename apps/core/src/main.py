import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apps.core.src.consumer import MessageConsumer
from apps.core.src.services.message_handler import MessageHandler
from shared.database.connection import init_db
import os


def setup_dependencies():
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    message_handler = MessageHandler()
    consumer = MessageConsumer(redis_url=redis_url, handler=message_handler)

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
