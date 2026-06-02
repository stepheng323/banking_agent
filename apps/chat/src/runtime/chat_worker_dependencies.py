"""Dependency loader for the ECS core chat runtime."""

from langchain_openai import ChatOpenAI

from apps.chat.src.agent.assistant_profile.loader import get_cached_assistant_profile
from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.config.dependencies import OrchestratorDependencies
from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.planning.task_planner_prompt_runtime import refresh_runtime_planner_system_prompt
from apps.chat.src.agent.orchestrator.services.media_service import MediaService
from apps.chat.src.agent.orchestrator.task_queue.service import TaskQueueService
from apps.chat.src.agent.workers.account.worker import AccountWorker
from apps.chat.src.agent.workers.airtime.extractor import AirtimeEntityExtractor
from apps.chat.src.agent.workers.airtime.worker import AirtimeWorker
from apps.chat.src.agent.workers.data.extraction.extractor import DataEntityExtractor
from apps.chat.src.agent.workers.data.worker import DataWorker as AgentDataWorker
from apps.chat.src.agent.workers.faq.worker import FAQWorker
from apps.chat.src.agent.workers.onboarding.executor import OnboardingExecutor
from apps.chat.src.agent.workers.onboarding.service import OnboardingService
from apps.chat.src.agent.workers.query.session import QuerySessionManager
from apps.chat.src.agent.workers.query.worker import QueryWorker as AgentQueryWorker
from apps.chat.src.agent.workers.support.worker import SupportWorker
from apps.chat.src.agent.workers.transfer.extraction.extractor import TransferEntityExtractor
from apps.chat.src.agent.workers.transfer.worker import TransferWorker
from apps.chat.src.queue_consumers.message_consumer import MessageConsumer
from apps.chat.src.runtime.common import build_messaging_clients
from banking.accounts.onboarding.runtime import session_manager as onboarding_session_manager
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.identity.repositories.user_repository import UserRepository
from banking.persistence.session_scoped import (
    SessionScopedAccountRepository,
    SessionScopedActionableMessageRepository,
    SessionScopedBankTransactionRepository,
    SessionScopedBeneficiaryRepository,
    SessionScopedTransactionRepository,
    SessionScopedUserRepository,
)
from banking.policy.guardrails.loader import get_cached_guardrails
from banking.policy.loader import get_cached_policy
from banking.policy.validation import validate_policy_coverage
from banking.presentation.i18n.renderer import validate_catalog_completeness
from banking.support.services.ticket_service import TicketService
from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.factories.providers import ProviderFactory
from shared.config.settings import settings
from shared.database.connection import get_db_session
from shared.queue.contracts import get_contract_by_topic
from shared.queue.factory import QueuePublisherFactory
from shared.queue.redis_stream_consumer import RedisStreamConsumer
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
    bank_transaction_repository = SessionScopedBankTransactionRepository(session_factory)
    transaction_repository = SessionScopedTransactionRepository(session_factory)

    user_data_cache = UserDataCache(redis_client=shared_redis)
    onboarding_service = OnboardingService(queue_publisher)
    onboarding_executor = OnboardingExecutor(user_repository, onboarding_service)

    beneficiary_suggestion_service = BeneficiarySuggestionService(queue_publisher)
    bank_data_provider = ProviderFactory.get_bank_data_provider()
    if bank_data_provider is None:
        raise RuntimeError(f"Account provider bank-data capability is not configured: {settings.account_provider_name}")
    resolver_provider = ProviderFactory.get_resolver_for_flow("transfer")
    if resolver_provider is None:
        raise RuntimeError(f"Transfer resolver provider is not configured: {settings.transfer_resolver_provider_name}")
    payout_resolver_provider = ProviderFactory.get_resolver_for_flow("payout")
    if payout_resolver_provider is None:
        raise RuntimeError(f"Payout resolver provider is not configured: {settings.payout_resolver_provider_name}")
    direct_debit_provider = ProviderFactory.get_direct_debit_provider()
    if direct_debit_provider is None:
        raise RuntimeError(
            f"Account provider direct-debit capability is not configured: {settings.account_provider_name}"
        )

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
        redis_client=shared_redis,
    )

    support_worker = SupportWorker(
        llm=llm,
        transaction_repo=transaction_repository,
        actionable_message_repo=actionable_message_repository,
        bank_transaction_repo=bank_transaction_repository,
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
    bank_cache_service = BankCacheService(redis_client=shared_redis, provider_name=resolver_provider.provider_name)
    payout_bank_cache_service = BankCacheService(
        redis_client=shared_redis,
        provider_name=payout_resolver_provider.provider_name,
    )

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
        payout_resolver_provider=payout_resolver_provider,
        payout_bank_cache=payout_bank_cache_service,
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
    refresh_runtime_planner_system_prompt()

    queue_publisher = QueuePublisherFactory.get_async_publisher()
    messaging_clients = build_messaging_clients()
    shared_redis = RedisClient.get_client()
    llm = ChatOpenAI(model=settings.planner_model, temperature=0, timeout=30.0, max_retries=1)
    app_env = settings.runtime.app_env
    logger.info("planner_model_selected", app_env=app_env, model=settings.planner_model)
    query_model = _resolve_role_model(
        role="query",
        configured_model=settings.query_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    query_llm = ChatOpenAI(model=query_model, temperature=0, timeout=30.0, max_retries=1)
    semantic_router_model = _resolve_role_model(
        role="semantic_router",
        configured_model=settings.semantic_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    semantic_router_llm = ChatOpenAI(model=semantic_router_model, temperature=0, timeout=15.0, max_retries=1)
    interrupt_router_model = _resolve_role_model(
        role="interrupt_router",
        configured_model=settings.interrupt_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )

    interrupt_llm = ChatOpenAI(model=interrupt_router_model, temperature=0, timeout=15.0, max_retries=1)
    extractor_model = _resolve_role_model(
        role="extractor",
        configured_model=settings.extractor_model,
        planner_model=settings.planner_model,
        app_env=app_env,
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
        group_name=f"{settings.project_name}-chat-worker-{settings.runtime.infrastructure_environment}",
    )
    return message_consumer, redis_stream_consumer
