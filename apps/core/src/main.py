"""Core Banking Service main module."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
import redis.asyncio as redis

from shared.config import settings

from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.connection import get_db_session, init_db, init_checkpoint_tables
from shared.queue.redis_queue import RedisQueue
from shared.repositories.user_repository import UserRepository
from shared.cache import UserContextCacheService, BankCacheService
from shared.cache.redis_client import RedisClient
from shared.clients.payment_provider_factory import PaymentProviderFactory

from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.consumer import MessageConsumer
from apps.core.src.services.message_handler import MessageHandler
from apps.core.src.services.onboarding.handler import OnboardingHandler
from apps.core.src.services.onboarding.onboarding_service import OnboardingService


def setup_dependencies():
    """Setup deps"""
    whatsapp_client = WhatsAppClient()
    redis_queue = RedisQueue(redis_url=settings.redis_url)
    user_repository = UserRepository(db=get_db_session())

    # Get shared Redis client (set in lifespan or auto-created)
    shared_redis = RedisClient.get_client()

    # Initialize cache services with shared Redis client
    user_cache = UserContextCacheService(redis_client=shared_redis)

    onboarding_service = OnboardingService(whatsapp_client)
    onboarding_handler = OnboardingHandler(
        whatsapp_client, user_repository, onboarding_service)

    orchestrator = OrchestratorAgent(
        user_repo=user_repository, user_cache=user_cache
    )

    message_handler = MessageHandler(
        whatsapp_client=whatsapp_client,
        user_repository=user_repository,
        onboarding_handler=onboarding_handler,
        orchestrator=orchestrator,
    )

    consumer = MessageConsumer(
        redis_queue=redis_queue, handler=message_handler)
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

    try:
        init_checkpoint_tables()
        print("   ✅ Checkpoint tables initialized")
    except Exception as e:
        print(f"   ⚠️  Checkpoint initialization warning: {e}")

    payment_provider = None
    try:
        print("   🔑 Warming up payment provider...")
        payment_provider = PaymentProviderFactory.get_provider_for_service(
            "resolve_account")

        if payment_provider:
            await payment_provider.warm_up_token()
            print(
                f"   ✅ {payment_provider.provider_name.title()} ready with cached token")
        else:
            print("   ⚠️  No payment provider available")
    except Exception as e:
        print(f"   ⚠️  Payment provider warmup warning: {e}")

    redis_client = None
    try:
        redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        # Set shared Redis client for all cache services
        RedisClient.set_client(redis_client)
        print("   ✅ Redis client initialized")
    except Exception as e:
        print(f"   ⚠️  Redis client initialization warning: {e}")

    # Warm up bank cache (after Redis is initialized)
    if redis_client:
        try:
            print("   🏦 Warming up bank cache...")
            bank_cache = BankCacheService(redis_client=redis_client)

            if payment_provider and hasattr(payment_provider, 'fetch_banks'):
                async def fetch_banks():
                    return await payment_provider.fetch_banks(country="NG")

                cache_ready = await bank_cache.ensure_banks_cached(fetch_banks)
                if cache_ready:
                    banks = await bank_cache.get_banks()
                    print(
                        f"   ✅ Bank cache ready ({len(banks) if banks else 0} banks)")
                else:
                    print("   ⚠️  Bank cache warmup failed")
            else:
                print("   ⚠️  Payment provider does not support bank list fetching")
        except Exception as e:
            print(f"   ⚠️  Bank cache warmup warning: {e}")

    consumer = setup_dependencies()
    asyncio.create_task(consumer.start())
    print("   ✅ Consumer started in background")

    yield

    print("\n📴 Shutting down Core Banking Service...")

    if payment_provider:
        try:
            await payment_provider.shutdown()
        except Exception as e:
            print(f"   ⚠️  Payment provider shutdown error: {e}")

    consumer.stop()
    await asyncio.sleep(0.5)
    print("   ✅ Services stopped")


app = FastAPI(title="Core Banking Service", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "Core Banking Service", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
