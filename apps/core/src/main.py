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
from shared.cache import BankCacheService
from shared.clients.payment_provider_factory import PaymentProviderFactory

from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.consumer import MessageConsumer
from apps.core.src.services.message_handler import MessageHandler
from apps.core.src.services.onboarding.handler import OnboardingHandler
from apps.core.src.services.onboarding.onboarding_service import OnboardingService
from apps.core.src.api.routes import admin as admin_routes


from apps.core.src.agent.banking.transfer.utils import (
    load_banks_from_flutterwave_response,
    fetch_and_cache_banks_on_startup
)


def setup_dependencies():
    """Setup deps"""
    whatsapp_client = WhatsAppClient()
    redis_queue = RedisQueue(redis_url=settings.redis_url)
    user_repository = UserRepository(db=get_db_session())

    orchestrator = OrchestratorAgent()

    onboarding_service = OnboardingService(whatsapp_client)
    onboarding_handler = OnboardingHandler(
        whatsapp_client, user_repository, onboarding_service)

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
        print("   ✅ Redis client initialized")
    except Exception as e:
        print(f"   ⚠️  Redis client initialization warning: {e}")

    try:
        if redis_client:
            admin_routes.set_redis_client(redis_client)

            bank_cache = BankCacheService(redis_client)
            banks = await fetch_and_cache_banks_on_startup(bank_cache)
            load_banks_from_flutterwave_response(banks)
            print(f"   ✅ Loaded {len(banks)} Nigerian banks for normalization")
        else:
            print("   ❌ Redis unavailable - bank normalization disabled")
    except Exception as e:
        print(f"   ⚠️  Bank loader error: {e}")

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

app.include_router(admin_routes.router)


@app.get("/")
async def root():
    return {"service": "Core Banking Service", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
