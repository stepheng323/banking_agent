"""Core Banking Service main module."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
import redis.asyncio as redis
from langchain_openai import ChatOpenAI

from shared.clients.s3_client import S3Client
from shared.config import settings

from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.connection import get_db_session, init_db, init_checkpoint_tables
from shared.queue.redis_queue import RedisQueue
from shared.repositories import BeneficiaryRepository, AccountRepository
from shared.repositories.user_repository import UserRepository
from shared.cache import UserContextCacheService, BankCacheService
from shared.cache.redis_client import RedisClient
from shared.clients.payment_provider_factory import PaymentProviderFactory
from shared.services.receipt_generator import ReceiptGenerator


from apps.core.src.agent.orchestrator import OrchestratorAgent
from apps.core.src.agent.services import ConversationResponder, TaskQueueService, TaskExecutor
from apps.core.src.agent.transfer import TransferService as AgentTransferService
from apps.core.src.agent.airtime import AirtimeService
from apps.core.src.queue_consumers import MessageConsumer, TransactionConsumer
from apps.core.src.handlers import (
    OnboardingHandler,
    OnboardingService,
    TransferHandler,
    TransferService
)
from apps.core.src.handlers.airtime import AirtimeHandler, AirtimeService as HandlerAirtimeService


def setup_dependencies():
    """Setup deps"""
    whatsapp_client = WhatsAppClient()
    redis_queue = RedisQueue(redis_url=settings.redis_url)
    user_repository = UserRepository(db=get_db_session())

    shared_redis = RedisClient.get_client()

    user_cache = UserContextCacheService(redis_client=shared_redis)

    onboarding_service = OnboardingService(whatsapp_client)
    onboarding_handler = OnboardingHandler(
        whatsapp_client, user_repository, onboarding_service)

    beneficiary_repository = BeneficiaryRepository(db=get_db_session())
    account_repository = AccountRepository(db=get_db_session())
    receipt_generator = ReceiptGenerator()
    s3_client = S3Client()

    transfer_service = TransferService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
        beneficiary_repository=beneficiary_repository,
        receipt_generator=receipt_generator,
        s3_client=s3_client,
    )


    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    task_queue_service = TaskQueueService()
    conversation_responder = ConversationResponder(llm)

    agent_transfer_service = AgentTransferService(
        llm=llm,
        user_cache=user_cache,
        beneficiary_repo=beneficiary_repository,
        account_repo=account_repository,
        whatsapp_client=whatsapp_client,
        completion_callback=None,
    )

    agent_airtime_service = AirtimeService(
        llm=llm,
        user_cache=user_cache,
        account_repo=account_repository,
        beneficiary_repo=beneficiary_repository,
        whatsapp_client=whatsapp_client,
        queue=redis_queue,
        completion_callback=None,
    )

    task_executor = TaskExecutor(
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        task_queue_service=task_queue_service,
        completion_callback=None,
    )

    orchestrator = OrchestratorAgent(
        llm=llm,
        user_repo=user_repository,
        user_cache=user_cache,
        whatsapp_client=whatsapp_client,
        task_queue_service=task_queue_service,
        conversation_responder=conversation_responder,
        transfer_service=agent_transfer_service,
        airtime_service=agent_airtime_service,
        task_executor=task_executor,
    )

    completion_callback = orchestrator.completion_callback
    agent_transfer_service.graph.completion_callback = completion_callback
    agent_airtime_service.graph.completion_callback = completion_callback
    task_executor.completion_callback = completion_callback

    message_consumer = MessageConsumer(
        redis_queue=redis_queue,
        user_repository=user_repository,
        onboarding_handler=onboarding_handler,
        orchestrator=orchestrator,
        whatsapp_client=whatsapp_client,
    )

    # Create handler services
    handler_airtime_service = HandlerAirtimeService(
        whatsapp_client=whatsapp_client,
        redis_client=shared_redis,
    )
    airtime_handler = AirtimeHandler(airtime_service=handler_airtime_service)
    transfer_handler = TransferHandler(transfer_service=transfer_service)

    # Create unified transaction consumer
    transaction_consumer = TransactionConsumer(
        redis_queue=redis_queue,
        transfer_handler=transfer_handler,
        airtime_handler=airtime_handler,
    )

    return message_consumer, transaction_consumer


@asynccontextmanager
async def lifespan(_app: FastAPI):
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
        RedisClient.set_client(redis_client)
        print("   ✅ Redis client initialized")
    except Exception as e:
        print(f"   ⚠️  Redis client initialization warning: {e}")

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
                        f"✅ Bank cache ready ({len(banks) if banks else 0} banks)")
                else:
                    print(" ⚠️ Bank cache warmup failed")
            else:
                print("   ⚠️  Payment provider does not support bank list fetching")
        except Exception as e:
            print(f"   ⚠️  Bank cache warmup warning: {e}")

    message_consumer, transaction_consumer = setup_dependencies()
    asyncio.create_task(message_consumer.start())
    print("   ✅ Message consumer started in background")
    asyncio.create_task(transaction_consumer.start())
    print("   ✅ Transaction consumer started in background")

    yield

    print("\n📴 Shutting down Core Banking Service...")

    if payment_provider:
        try:
            await payment_provider.shutdown()
        except Exception as e:
            print(f"   ⚠️  Payment provider shutdown error: {e}")

    message_consumer.stop()
    transaction_consumer.stop()
    await asyncio.sleep(0.5)
    print("   ✅ Services stopped")


app = FastAPI(title="Core Banking Service", lifespan=lifespan)


@app.get("/")
async def root():
    """Root endpoint"""
    return {"service": "Core Banking Service", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}
