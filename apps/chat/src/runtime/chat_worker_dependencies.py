"""Dependency loader for the ECS core chat runtime."""

from langchain_openai import ChatOpenAI

from apps.chat.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService
from apps.chat.src.agent.graphs.account import AccountWorker
from apps.chat.src.agent.graphs.airtime import AirtimeWorker
from apps.chat.src.agent.graphs.airtime.extractor import AirtimeEntityExtractor
from apps.chat.src.agent.graphs.data import DataWorker as AgentDataWorker
from apps.chat.src.agent.graphs.data.extractor import DataEntityExtractor
from apps.chat.src.agent.graphs.faq import FAQWorker
from apps.chat.src.agent.graphs.onboarding.executor import OnboardingExecutor
from apps.chat.src.agent.graphs.onboarding.service import OnboardingService
from apps.chat.src.agent.graphs.query.session import QuerySessionManager
from apps.chat.src.agent.graphs.query.worker import QueryWorker as AgentQueryWorker
from apps.chat.src.agent.graphs.support import SupportWorker
from apps.chat.src.agent.graphs.transfer import TransferWorker
from apps.chat.src.agent.graphs.transfer.services.extractor import TransferEntityExtractor
from apps.chat.src.agent.orchestrator.config import OrchestratorDependencies
from apps.chat.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.services.media_service import MediaService
from apps.chat.src.queue_consumers.message_consumer import MessageConsumer
from apps.chat.src.runtime.common import build_messaging_clients
from shared.assistant_profile.loader import get_cached_assistant_profile
from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.factories.providers import ProviderFactory
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
from shared.config.settings import settings
from shared.database.connection import get_db_session
from shared.guardrails.loader import get_cached_guardrails
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


def _resolve_role_model(*, role: str, configured_model: str, planner_model: str, app_env: str) -> str:
    """Resolve role model from env and emit actionable overlap warnings."""
    role_env_map = {
        "query": "QUERY_MODEL",
        "semantic_router": "SEMANTIC_ROUTER_MODEL",
        "interrupt_router": "INTERRUPT_ROUTER_MODEL",
        "extractor": "EXTRACTOR_MODEL",
    }
    recommended_model_map = {
        "query": None,
        "semantic_router": "gpt-5.4-nano",
        "interrupt_router": "gpt-5.4-nano",
        "extractor": "gpt-5.4-mini",
    }
    env_var = role_env_map[role]
    recommended_model = recommended_model_map[role]
    model = configured_model.strip()
    if not model:
        model = planner_model
        logger.warning(
            f"{role}_model_missing_fallback",
            app_env=app_env,
            fallback_model=model,
            planner_model=planner_model,
            recommended_env=env_var,
            recommended_model=recommended_model,
        )

    if model == planner_model:
        logger.warning(
            f"{role}_model_same_as_planner",
            app_env=app_env,
            model=model,
            planner_model=planner_model,
            recommended_env=env_var,
            dedicated=False,
            recommended_model=recommended_model,
        )
    else:
        logger.info(
            f"{role}_model_dedicated",
            app_env=app_env,
            model=model,
            planner_model=planner_model,
            recommended_env=env_var,
            dedicated=True,
            recommended_model=recommended_model,
        )
    return model


def _build_orchestrator_runtime_bundle(
    queue_publisher,
    messaging_clients,
    shared_redis,
    llm,
    query_llm,
    semantic_router_llm: ChatOpenAI | None = None,
    interrupt_llm: ChatOpenAI | None = None,
    extractor_llm: ChatOpenAI | None = None,
) -> tuple[UserRepository, OnboardingExecutor, OrchestratorAgent]:
    """Build one isolated runtime bundle without a shared DB session."""
    session_factory = get_db_session
    logger.info("chat_worker_runtime_db_access_mode", mode="session_scoped")
    extractor_chat = extractor_llm or llm

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
        extractor=DataEntityExtractor(llm=extractor_chat),
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
        extractor=AirtimeEntityExtractor(llm=extractor_chat),
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
        extractor=TransferEntityExtractor(llm=extractor_chat),
        resolver_provider=resolver_provider,
        bank_cache=bank_cache_service,
        transaction_repo=transaction_repository,
        dd_provider=direct_debit_provider,
        redis_client=shared_redis,
    )

    media_service = MediaService(messaging_clients)

    orchestrator_deps = OrchestratorDependencies(
        llm=llm,
        semantic_router_llm=semantic_router_llm,
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
    semantic_router_llm: ChatOpenAI | None = None,
    interrupt_llm: ChatOpenAI | None = None,
    extractor_llm: ChatOpenAI | None = None,
):
    """Build a factory that returns runtime dependencies with short-lived DB access."""

    def _factory() -> tuple[UserRepository, OnboardingExecutor, OrchestratorAgent]:
        return _build_orchestrator_runtime_bundle(
            queue_publisher=queue_publisher,
            messaging_clients=messaging_clients,
            shared_redis=shared_redis,
            llm=llm,
            query_llm=query_llm,
            semantic_router_llm=semantic_router_llm,
            interrupt_llm=interrupt_llm,
            extractor_llm=extractor_llm,
        )

    return _factory


def setup_chat_consumers() -> tuple[MessageConsumer, RedisStreamConsumer]:
    """Setup chat worker runtime dependencies."""
    validate_catalog_completeness()
    capability_policy = get_cached_policy(force_reload=True)
    validate_policy_coverage(capability_policy)
    get_cached_assistant_profile(force_reload=True)
    get_cached_guardrails(force_reload=True)
    refresh_planner_system_prompt()

    queue_publisher = QueuePublisherFactory.get_async_publisher()
    messaging_clients = build_messaging_clients()
    shared_redis = RedisClient.get_client()
    llm = ChatOpenAI(model=settings.planner_model, temperature=0, timeout=30.0, max_retries=1)
    logger.info("planner_model_selected", app_env=settings.app_env, model=settings.planner_model)
    query_model = _resolve_role_model(
        role="query",
        configured_model=settings.query_model,
        planner_model=settings.planner_model,
        app_env=settings.app_env,
    )
    query_llm = ChatOpenAI(model=query_model, temperature=0, timeout=30.0, max_retries=1)
    semantic_router_model = _resolve_role_model(
        role="semantic_router",
        configured_model=settings.semantic_router_model,
        planner_model=settings.planner_model,
        app_env=settings.app_env,
    )
    semantic_router_llm = ChatOpenAI(model=semantic_router_model, temperature=0, timeout=15.0, max_retries=1)
    interrupt_router_model = _resolve_role_model(
        role="interrupt_router",
        configured_model=settings.interrupt_router_model,
        planner_model=settings.planner_model,
        app_env=settings.app_env,
    )

    interrupt_llm = ChatOpenAI(model=interrupt_router_model, temperature=0, timeout=15.0, max_retries=1)
    extractor_model = _resolve_role_model(
        role="extractor",
        configured_model=settings.extractor_model,
        planner_model=settings.planner_model,
        app_env=settings.app_env,
    )
    extractor_llm = ChatOpenAI(model=extractor_model, temperature=0, timeout=20.0, max_retries=1)
    logger.info(
        "chat_worker_role_models_resolved",
        planner_model=settings.planner_model,
        query_model=query_model,
        semantic_router_model=semantic_router_model,
        interrupt_router_model=interrupt_router_model,
        extractor_model=extractor_model,
    )

    runtime_bundle_factory = _build_runtime_bundle_factory(
        queue_publisher=queue_publisher,
        messaging_clients=messaging_clients,
        shared_redis=shared_redis,
        llm=llm,
        query_llm=query_llm,
        semantic_router_llm=semantic_router_llm,
        interrupt_llm=interrupt_llm,
        extractor_llm=extractor_llm,
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
        group_name=f"{settings.project_name}-chat-worker-{settings.environment}",
    )
    return message_consumer, redis_stream_consumer


def setup_core_consumers() -> tuple[MessageConsumer, RedisStreamConsumer]:
    """Compatibility alias for the renamed chat worker dependency entrypoint."""
    return setup_chat_consumers()
