"""Inbound identity and delivery bookkeeping for chat messages."""

from typing import Any

from apps.chat.src.agent.orchestrator import OrchestratorAgent
from apps.chat.src.queue_consumers.channel_link_gate import looks_like_phone_number
from shared.cache.channel_identity_cache import load_channel_identity_user, store_channel_identity_user
from shared.config.settings import settings
from shared.messaging.prompt_suppression import latest_inbound_delivery_target_key
from banking.identity.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


async def resolve_channel_user(
    *,
    user_repository: UserRepository,
    channel: str,
    channel_user_id: str,
) -> Any | None:
    if channel == "whatsapp" and looks_like_phone_number(channel_user_id):
        return await user_repository.get_by_phone(channel_user_id)

    cached_user = await load_channel_identity_user(channel, channel_user_id)
    if cached_user is not None:
        return cached_user

    user = await user_repository.get_by_channel_identity(channel, channel_user_id)
    if user is not None:
        await store_channel_identity_user(channel, channel_user_id, user)
    return user


def resolve_latest_inbound_redis_client(
    *,
    configured_redis_client: Any | None,
    orchestrator: OrchestratorAgent | None,
) -> Any | None:
    """Use the chat runtime Redis client when available; tests can omit it."""
    if configured_redis_client is not None:
        return configured_redis_client
    if orchestrator is None:
        return None

    deps = getattr(orchestrator, "deps", None)
    redis_client = getattr(deps, "redis_client", None)
    if redis_client is not None:
        return redis_client

    handler = getattr(orchestrator, "orchestrator_handler", None)
    return getattr(handler, "redis_client", None)


async def record_latest_inbound_for_delivery_target(
    *,
    configured_redis_client: Any | None,
    orchestrator: OrchestratorAgent | None,
    channel: str,
    delivery_target: str,
    message_id: str,
) -> None:
    redis_client = resolve_latest_inbound_redis_client(
        configured_redis_client=configured_redis_client,
        orchestrator=orchestrator,
    )
    if redis_client is None:
        return

    key = latest_inbound_delivery_target_key(channel, delivery_target)
    try:
        await redis_client.set(
            key,
            str(message_id),
            ex=max(1, settings.chat_latest_inbound_ttl_seconds),
        )
    except Exception as exc:
        logger.warning(
            "message_consumer_latest_inbound_record_failed",
            channel=channel,
            channel_user_id=delivery_target,
            message_id_hash=log_fingerprint(message_id),
            error=str(exc),
        )
