"""Receipt Worker Service - FastAPI entry point with uvicorn auto-reload."""

import asyncio
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from apps.receipt_worker.src.consumer import ReceiptJobConsumer
from shared.cache.redis_client import RedisClient
from shared.config import settings
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting Receipt Worker Service...")

    # Initialize Redis client
    redis_client = None
    try:
        redis_client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        RedisClient.set_client(redis_client)
        logger.info("Redis client initialized")
    except Exception as e:
        logger.warning("Redis client initialization warning", error=str(e))

    # Start consumer in background
    consumer = ReceiptJobConsumer()
    asyncio.create_task(consumer.start())
    logger.info("Receipt consumer started in background")

    yield

    # Shutdown
    logger.info("Shutting down Receipt Worker Service...")
    await consumer.stop()
    await asyncio.sleep(0.5)
    logger.info("Receipt worker stopped")


app = FastAPI(title="Receipt Worker Service", lifespan=lifespan)


@app.get("/")
async def root():
    """Root endpoint"""
    return {"service": "Receipt Worker Service", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}
