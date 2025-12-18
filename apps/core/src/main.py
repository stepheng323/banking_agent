"""Core Banking Service main module."""
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
import redis.asyncio as redis

from shared.config import settings
from shared.database.connection import init_db
from shared.cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.clients.payment_provider_factory import PaymentProviderFactory

from apps.core.src.dependencies import setup_dependencies


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting Core Banking Service...")

    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.warning("Database initialization warning", error=str(e))

    payment_provider = None
    try:
        logger.info("Warming up payment provider...")
        payment_provider = PaymentProviderFactory.get_provider_for_service(
            "resolve_account")

        if payment_provider:
            await payment_provider.warm_up_token()
            logger.info(f"{payment_provider.provider_name.title()} ready with cached token")
        else:
            logger.warning("No payment provider available")
    except Exception as e:
        logger.warning("Payment provider warmup warning", error=str(e))

    redis_client = None
    try:
        redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        RedisClient.set_client(redis_client)
        logger.info("Redis client initialized")
    except Exception as e:
        logger.warning("Redis client initialization warning", error=str(e))

    if redis_client:
        try:
            logger.info("Warming up bank cache...")
            bank_cache = BankCacheService(redis_client=redis_client)

            if payment_provider and hasattr(payment_provider, 'fetch_banks'):
                async def fetch_banks():
                    return await payment_provider.fetch_banks(country="NG")

                cache_ready = await bank_cache.ensure_banks_cached(fetch_banks)
                if cache_ready:
                    banks = await bank_cache.get_banks()
                    logger.info("Bank cache ready", count=len(banks) if banks else 0)
                else:
                    logger.warning("Bank cache warmup failed")
            else:
                logger.warning("Payment provider does not support bank list fetching")
        except Exception as e:
            logger.warning("Bank cache warmup warning", error=str(e))

    message_consumer, transaction_consumer, flow_event_consumer = setup_dependencies()
    asyncio.create_task(message_consumer.start())
    logger.info("Message consumer started in background")
    asyncio.create_task(transaction_consumer.start())
    logger.info("Transaction consumer started in background")
    asyncio.create_task(flow_event_consumer.start())
    logger.info("Flow event consumer started in background")

    yield

    print("\n")     
    logger.info("Shutting down Core Banking Service...")

    if payment_provider:
        try:
            await payment_provider.shutdown()
        except Exception as e:
            logger.warning("Payment provider shutdown error", error=str(e))

    message_consumer.stop()
    transaction_consumer.stop()
    flow_event_consumer.stop()
    await asyncio.sleep(0.5)
    logger.info("Services stopped")


app = FastAPI(title="Core Banking Service", lifespan=lifespan)


@app.get("/")
async def root():
    """Root endpoint"""
    return {"service": "Core Banking Service", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}
