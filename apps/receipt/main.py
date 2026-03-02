import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.receipt.src.consumer import ReceiptJobConsumer
from shared.cache.redis_client import RedisClient
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)


async def _worker_loop(consumer: ReceiptJobConsumer):
    """Background worker loop for processing receipt jobs."""
    logger.info("receipt_worker_loop_starting")
    while True:
        try:
            # Note: SQS consume_one logic would normally go here if running as a standalone ECS worker
            # For now, this is a placeholder as the receipt service is primarily event-driven by Lambda
            await asyncio.sleep(60)
        except Exception as e:
            logger.error("receipt_worker_loop_error", error=str(e))
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown logic for the receipt service."""
    logger.info("receipt_service_starting")

    # Initialize shared resources
    RedisClient.get_client()
    # Start background worker if configured (optional for local dev)
    # Background worker logic can be added here

    yield

    logger.info("receipt_service_shutting_down")


app = FastAPI(title="Receipt Service", lifespan=lifespan)


@app.get("/")
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "receipt"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
