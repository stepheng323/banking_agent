"""Runtime bundle construction for chat orchestrator consumers."""

from collections.abc import Callable
from typing import TypeAlias

import redis.asyncio as redis
from langchain_openai import ChatOpenAI

from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.config.dependencies import OrchestratorDependencies
from apps.chat.src.runtime.domain_services import build_chat_domain_services
from apps.chat.src.runtime.providers import build_chat_runtime_providers
from apps.chat.src.runtime.repositories import build_chat_runtime_repositories
from banking.accounts.onboarding.executor import OnboardingExecutor
from banking.identity.repositories.user_repository import UserRepository
from shared.clients.abstractions.messaging import MessagingClient
from shared.database.connection import get_db_session
from shared.queue.adapter import QueuePublisher

ChatRuntimeBundle: TypeAlias = tuple[UserRepository, OnboardingExecutor, OrchestratorAgent]
ChatRuntimeBundleFactory: TypeAlias = Callable[[], ChatRuntimeBundle]


def build_orchestrator_runtime_bundle(
    queue_publisher: QueuePublisher,
    messaging_clients: dict[str, MessagingClient],
    shared_redis: redis.Redis,
    llm: ChatOpenAI,
    query_llm: ChatOpenAI,
    semantic_router_llm: ChatOpenAI | None = None,
    conversation_llm: ChatOpenAI | None = None,
    interrupt_llm: ChatOpenAI | None = None,
    extractor_llm: ChatOpenAI | None = None,
) -> ChatRuntimeBundle:
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
        conversation_llm=conversation_llm or semantic_router_llm or llm,
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
        task_state_service=services.task_state_service,
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


def build_runtime_bundle_factory(
    *,
    queue_publisher: QueuePublisher,
    messaging_clients: dict[str, MessagingClient],
    shared_redis: redis.Redis,
    llm: ChatOpenAI,
    query_llm: ChatOpenAI,
    semantic_router_llm: ChatOpenAI | None = None,
    conversation_llm: ChatOpenAI | None = None,
    interrupt_llm: ChatOpenAI | None = None,
    extractor_llm: ChatOpenAI | None = None,
) -> ChatRuntimeBundleFactory:
    """Build a factory that returns runtime dependencies with short-lived DB access."""

    def _factory() -> ChatRuntimeBundle:
        return build_orchestrator_runtime_bundle(
            queue_publisher=queue_publisher,
            messaging_clients=messaging_clients,
            shared_redis=shared_redis,
            llm=llm,
            query_llm=query_llm,
            semantic_router_llm=semantic_router_llm,
            conversation_llm=conversation_llm,
            interrupt_llm=interrupt_llm,
            extractor_llm=extractor_llm,
        )

    return _factory
