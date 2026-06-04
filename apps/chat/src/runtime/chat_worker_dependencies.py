"""Dependency loader for the ECS core chat runtime."""

from langchain_openai import ChatOpenAI

from apps.chat.src.agent.assistant_profile.loader import get_cached_assistant_profile
from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.config.dependencies import OrchestratorDependencies
from apps.chat.src.agent.orchestrator.planning.task_planner_prompt_runtime import refresh_runtime_planner_system_prompt
from apps.chat.src.queue_consumers.message_consumer import MessageConsumer
from apps.chat.src.runtime.common import build_messaging_clients
from apps.chat.src.runtime.domain_services import build_chat_domain_services
from apps.chat.src.runtime.model_roles import build_chat_role_models
from apps.chat.src.runtime.providers import build_chat_runtime_providers
from apps.chat.src.runtime.repositories import build_chat_runtime_repositories
from banking.accounts.onboarding.executor import OnboardingExecutor
from banking.identity.repositories.user_repository import UserRepository
from banking.policy.guardrails.loader import get_cached_guardrails
from banking.policy.loader import get_cached_policy
from banking.policy.validation import validate_policy_coverage
from banking.presentation.i18n.renderer import validate_catalog_completeness
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.database.connection import get_db_session
from shared.queue.contracts import get_contract_by_topic
from shared.queue.factory import QueuePublisherFactory
from shared.queue.redis_stream_consumer import RedisStreamConsumer


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
    repositories = build_chat_runtime_repositories(session_factory)
    providers = build_chat_runtime_providers()
    services = build_chat_domain_services(
        repositories=repositories,
        providers=providers,
        queue_publisher=queue_publisher,
        messaging_clients=messaging_clients,
        shared_redis=shared_redis,
        llm=llm,
        query_llm=query_llm,
        extractor_llm=extractor_llm or llm,
        session_factory=session_factory,
    )

    orchestrator_deps = OrchestratorDependencies(
        llm=llm,
        semantic_router_llm=semantic_router_llm,
        interrupt_llm=interrupt_llm,
        user_repo=repositories.user,
        beneficiary_repo=repositories.beneficiary,
        actionable_message_repo=repositories.actionable_message,
        task_queue_service=services.task_queue_service,
        conversation_responder=services.conversation_responder,
        transfer_service=services.transfer_worker,
        airtime_service=services.airtime_worker,
        query_service=services.query_worker,
        support_service=services.support_worker,
        account_service=services.account_worker,
        beneficiary_service=services.beneficiary_worker,
        media_service=services.media_service,
        data_service=services.data_worker,
        user_cache=services.user_data_cache,
        faq_service=services.faq_worker,
        publisher=queue_publisher,
        beneficiary_suggestion_service=services.beneficiary_suggestion_service,
        account_repo=repositories.account,
        redis_client=shared_redis,
        banking_provider=providers.bank_data_provider,
    )

    return repositories.user, services.onboarding_executor, OrchestratorAgent(orchestrator_deps)


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
    role_models = build_chat_role_models(
        planner_model=settings.planner_model,
        query_model=settings.query_model,
        semantic_router_model=settings.semantic_router_model,
        interrupt_router_model=settings.interrupt_router_model,
        extractor_model=settings.extractor_model,
        app_env=settings.runtime.app_env,
    )

    runtime_bundle_factory = _build_runtime_bundle_factory(
        queue_publisher=queue_publisher,
        messaging_clients=messaging_clients,
        shared_redis=shared_redis,
        llm=role_models.planner_llm,
        query_llm=role_models.query_llm,
        semantic_router_llm=role_models.semantic_router_llm,
        interrupt_llm=role_models.interrupt_llm,
        extractor_llm=role_models.extractor_llm,
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
