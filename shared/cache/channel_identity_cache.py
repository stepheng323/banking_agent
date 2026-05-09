"""Cache helpers for channel identity lookups."""

import json
from types import SimpleNamespace
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)

CHANNEL_IDENTITY_CACHE_TTL_SECONDS = 3600
_CACHEABLE_CHANNELS = {"telegram"}


def channel_identity_cache_key(channel: str, channel_user_id: str) -> str:
    return f"cache:channel_identity:{channel}:{channel_user_id}"


def _serialize_cached_identity(user: Any) -> dict[str, str | None]:
    onboarding_status = getattr(user, "onboarding_status", None)
    if hasattr(onboarding_status, "value"):
        onboarding_status = onboarding_status.value
    return {
        "id": str(getattr(user, "id", "")) or None,
        "phone_number": str(getattr(user, "phone_number", "")) or None,
        "onboarding_status": str(onboarding_status or "") or None,
        "full_name": str(getattr(user, "full_name", "")) or None,
    }


def _hydrate_cached_identity(payload: dict[str, Any]) -> Any | None:
    phone_number = payload.get("phone_number")
    if not isinstance(phone_number, str) or not phone_number:
        return None
    return SimpleNamespace(
        id=payload.get("id"),
        phone_number=phone_number,
        onboarding_status=payload.get("onboarding_status"),
        full_name=payload.get("full_name"),
    )


async def load_channel_identity_user(channel: str, channel_user_id: str) -> Any | None:
    if channel not in _CACHEABLE_CHANNELS:
        return None
    try:
        redis_client = RedisClient.get_client()
        cached = await redis_client.get(channel_identity_cache_key(channel, channel_user_id))
        if not cached:
            return None
        payload = json.loads(cached)
        user = _hydrate_cached_identity(payload if isinstance(payload, dict) else {})
        if user is not None:
            logger.info("channel_identity_cache_hit", channel=channel, channel_user_id=channel_user_id)
        return user
    except Exception as exc:
        logger.warning(
            "channel_identity_cache_read_error",
            channel=channel,
            channel_user_id=channel_user_id,
            error=str(exc),
        )
        return None


async def store_channel_identity_user(channel: str, channel_user_id: str, user: Any) -> None:
    if channel not in _CACHEABLE_CHANNELS:
        return
    try:
        redis_client = RedisClient.get_client()
        await redis_client.set(
            channel_identity_cache_key(channel, channel_user_id),
            json.dumps(_serialize_cached_identity(user)),
            ex=CHANNEL_IDENTITY_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "channel_identity_cache_write_error",
            channel=channel,
            channel_user_id=channel_user_id,
            error=str(exc),
        )
