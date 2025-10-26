import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apps.core.src.consumer import MessageConsumer
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    print("🚀 Starting Core Banking Service...")

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    consumer = MessageConsumer(redis_url)

    task = asyncio.create_task(consumer.start())
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
