"""Dependency loader for the ECS core chat runtime."""

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.core.src.agent.graphs.account import AccountWorker
from apps.core.src.agent.graphs.airtime import AirtimeWorker
from apps.core.src.agent.graphs.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.graphs.data import DataWorker as AgentDataWorker
from apps.core.src.agent.graphs.data.extractor import DataEntityExtractor
from apps.core.src.agent.graphs.faq import FAQWorker
from apps.core.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.core.src.agent.graphs.onboarding.service import OnboardingService
from apps.core.src.agent.graphs.query.session import QuerySessionManager
from apps.core.src.agent.graphs.query.worker import QueryWorker as AgentQueryWorker
from apps.core.src.agent.graphs.support import SupportWorker
from apps.core.src.agent.graphs.transfer import TransferWorker
from apps.core.src.agent.graphs.transfer.services.extractor import TransferEntityExtractor
from apps.core.src.agent.orchestrator.config import OrchestratorDependencies
from apps.core.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from apps.core.src.agent.orchestrator.services.media_service import MediaService
from apps.core.src.queue_consumers.message_consumer import MessageConsumer
from apps.core.src.runtime.common import build_messaging_clients
from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.factories.providers import ProviderFactory
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
from shared.config.settings import settings
from shared.database.connection import get_db_session
from shared.i18n import validate_catalog_completeness
from shared.policy.loader import get_cached_policy
from shared.policy.validation import validate_policy_coverage
from shared.queue.contracts import get_contract_by_topic
from shared.queue.factory import QueuePublisherFactory
from shared.queue.redis_stream_consumer import RedisStreamConsumer
from shared.repositories.session_scoped import (
    SessionScopedAccountRepository,
    SessionScopedActionableMessageRepository,
    SessionScopedBeneficiaryRepository,
    SessionScopedTransactionRepository,
    SessionScopedUserRepository,
)
from shared.repositories.user_repository import UserRepository
from shared.services.conversation_responder import ConversationResponder
from shared.services.onboarding import session_manager as onboarding_session_manager
from shared.services.task_planner import refresh_planner_system_prompt
from shared.services.task_queue.service import TaskQueueService
from shared.services.ticket_service import TicketService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_orchestrator_runtime_bundle(
    queue_publisher,
    messaging_clients,
    shared_redis,
    llm,
    query_llm,
    interrupt_llm: ChatOpenAI | None = None,
) -> tuple[UserRepository, OnboardingExecutor, OrchestratorAgent]:
    """Build one isolated runtime bundle without a shared DB session."""
    session_factory = get_db_session
    logger.info("core_chat_runtime_db_access_mode", mode="session_scoped")

    user_repository = SessionScopedUserRepository(session_factory)
    beneficiary_repository = SessionScopedBeneficiaryRepository(session_factory)
    account_repository = SessionScopedAccountRepository(session_factory)
    actionable_message_repository = SessionScopedActionableMessageRepository(session_factory)
    transaction_repository = SessionScopedTransactionRepository(session_factory)

    user_data_cache = UserDataCache(redis_client=shared_redis)
    onboarding_service = OnboardingService(queue_publisher)
    onboarding_executor = OnboardingExecutor(user_repository, onboarding_service)

    beneficiary_suggestion_service = BeneficiarySuggestionService(queue_publisher)
    bank_data_provider = ProviderFactory.get_bank_data_provider("mono")
    if bank_data_provider is None:
        raise RuntimeError("Mono bank data provider is not configured")
    resolver_provider = ProviderFactory.get_resolver_for_flow("transfer")
    if resolver_provider is None:
        raise RuntimeError("Transfer resolver provider is not configured")
    direct_debit_provider = MonoDirectDebitProvider()

    account_worker = AccountWorker(
        account_repo=account_repository,
        user_repo=user_repository,
        llm=llm,
        banking_provider=bank_data_provider,
        session_manager=onboarding_session_manager,
        direct_debit_provider=direct_debit_provider,
    )

    bill_provider = ProviderFactory.get_bill_provider()
    if bill_provider is None:
        raise RuntimeError("Bill provider is not configured")
    data_worker = AgentDataWorker(
        extractor=DataEntityExtractor(llm=llm),
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
    )

    support_worker = SupportWorker(
        llm=llm,
        transaction_repo=transaction_repository,
        actionable_message_repo=actionable_message_repository,
        redis_client=shared_redis,
        ticket_service=TicketService(session_factory=session_factory),
    )

    query_session_manager = QuerySessionManager(shared_redis)
    query_worker = AgentQueryWorker(
        llm=query_llm,
        banking_provider=bank_data_provider,
        session_manager=query_session_manager,
    )

    task_queue_service = TaskQueueService()
    conversation_responder = ConversationResponder(llm)
    bank_cache_service = BankCacheService(redis_client=shared_redis)

    agent_airtime_worker = AirtimeWorker(
        extractor=AirtimeEntityExtractor(llm=llm),
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
    )

    faq_worker = FAQWorker(
        llm=llm,
        get_db=get_db_session,
    )

    agent_transfer_worker = TransferWorker(
        validation_service=None,
        publisher=queue_publisher,
        extractor=TransferEntityExtractor(llm=llm),
        resolver_provider=resolver_provider,
        bank_cache=bank_cache_service,
        transaction_repo=transaction_repository,
        dd_provider=direct_debit_provider,
        redis_client=shared_redis,
    )

    media_service = MediaService(messaging_clients)

    orchestrator_deps = OrchestratorDependencies(
        llm=llm,
        interrupt_llm=interrupt_llm,
        user_repo=user_repository,
        beneficiary_repo=beneficiary_repository,
        actionable_message_repo=actionable_message_repository,
        task_queue_service=task_queue_service,
        conversation_responder=conversation_responder,
        transfer_service=agent_transfer_worker,
        airtime_service=agent_airtime_worker,
        query_service=query_worker,
        support_service=support_worker,
        account_service=account_worker,
        media_service=media_service,
        data_service=data_worker,
        user_cache=user_data_cache,
        faq_service=faq_worker,
        publisher=queue_publisher,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
        account_repo=account_repository,
        redis_client=shared_redis,
        banking_provider=bank_data_provider,
    )

    return user_repository, onboarding_executor, OrchestratorAgent(orchestrator_deps)


def _build_runtime_bundle_factory(
    *,
    queue_publisher,
    messaging_clients,
    shared_redis,
    llm,
    query_llm,
    interrupt_llm: ChatOpenAI | None = None,
):
    """Build a factory that returns runtime dependencies with short-lived DB access."""

    def _factory() -> tuple[UserRepository, OnboardingExecutor, OrchestratorAgent]:
        return _build_orchestrator_runtime_bundle(
            queue_publisher=queue_publisher,
            messaging_clients=messaging_clients,
            shared_redis=shared_redis,
            llm=llm,
            query_llm=query_llm,
            interrupt_llm=interrupt_llm,
        )

    return _factory


def setup_core_consumers() -> tuple[MessageConsumer, RedisStreamConsumer]:
    """Setup core ECS chat runtime dependencies."""
    validate_catalog_completeness()
    policy = get_cached_policy(force_reload=True)
    validate_policy_coverage(policy)
    refresh_planner_system_prompt()

    queue_publisher = QueuePublisherFactory.get_async_publisher()
    messaging_clients = build_messaging_clients()
    shared_redis = RedisClient.get_client()
    llm = ChatOpenAI(model=settings.planner_model, temperature=0, timeout=30.0, max_retries=1)
    query_model = settings.query_model.strip()
    if not query_model:
        query_model = settings.planner_model
        logger.warning(
            "query_model_missing_fallback",
            app_env=settings.app_env,
            fallback_model=query_model,
        )
    if query_model == settings.planner_model:
        logger.warning(
            "query_model_same_as_planner",
            app_env=settings.app_env,
            model=query_model,
        )
    query_llm = ChatOpenAI(model=query_model, temperature=0, timeout=30.0, max_retries=1)
    interrupt_router_model = settings.interrupt_router_model.strip()
    if not interrupt_router_model:
        interrupt_router_model = settings.planner_model
        logger.warning(
            "interrupt_router_model_missing_fallback",
            app_env=settings.app_env,
            fallback_model=interrupt_router_model,
        )
    if interrupt_router_model == settings.planner_model:
        logger.warning(
            "interrupt_router_model_same_as_planner",
            app_env=settings.app_env,
            model=interrupt_router_model,
        )

    interrupt_llm = ChatOpenAI(model=interrupt_router_model, temperature=0, timeout=15.0, max_retries=1)

    runtime_bundle_factory = _build_runtime_bundle_factory(
        queue_publisher=queue_publisher,
        messaging_clients=messaging_clients,
        shared_redis=shared_redis,
        llm=llm,
        query_llm=query_llm,
        interrupt_llm=interrupt_llm,
    )

    message_consumer = MessageConsumer(
        publisher=queue_publisher,
        user_repository=None,
        onboarding_executor=None,
        orchestrator=None,
        runtime_bundle_factory=runtime_bundle_factory,
    )

    stream_names = [
        get_contract_by_topic("message.received").redis_stream_name,
        get_contract_by_topic("flow_event.process").redis_stream_name,
    ]
    redis_stream_consumer = RedisStreamConsumer(
        stream_names=[name for name in stream_names if name],
        group_name=f"{settings.project_name}-core-chat-{settings.environment}",
    )
    return message_consumer, redis_stream_consumer
