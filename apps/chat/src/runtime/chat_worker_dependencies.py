"""Dependency loader for the ECS core chat runtime."""

from apps.chat.src.agent.assistant_profile.loader import get_cached_assistant_profile
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_runtime import (
    refresh_runtime_planner_system_prompt,
)
from apps.chat.src.queue_consumers.message_consumer import MessageConsumer
from apps.chat.src.runtime.bundles import build_runtime_bundle_factory
from apps.chat.src.runtime.common import build_messaging_clients
from apps.chat.src.runtime.model_roles import build_chat_role_models
from banking.policy.guardrails.loader import get_cached_guardrails
from banking.policy.loader import get_cached_policy
from banking.policy.validation import validate_policy_coverage
from banking.presentation.i18n.renderer import validate_catalog_completeness
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.queue.contracts import get_contract_by_topic
from shared.queue.factory import QueuePublisherFactory
from shared.queue.redis_stream_consumer import RedisStreamConsumer


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
        conversation_model=settings.conversation_model,
        interrupt_router_model=settings.interrupt_router_model,
        extractor_model=settings.extractor_model,
        app_env=settings.runtime.app_env,
    )

    runtime_bundle_factory = build_runtime_bundle_factory(
        queue_publisher=queue_publisher,
        messaging_clients=messaging_clients,
        shared_redis=shared_redis,
        llm=role_models.planner_llm,
        query_llm=role_models.query_llm,
        semantic_router_llm=role_models.semantic_router_llm,
        conversation_llm=role_models.conversation_llm,
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
