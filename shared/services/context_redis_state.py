"""Redis-backed conversation and message state helpers."""

import json
from collections.abc import Awaitable
from typing import Any, cast

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


def _user_key(phone_number: str, suffix: str) -> str:
    return f"user:{phone_number}:{suffix}"


async def get_conversation_state(phone_number: str) -> dict[str, Any] | None:
    try:
        redis_client = RedisClient.get_client()
        data = await redis_client.get(_user_key(phone_number, "conversation_state"))
        if data:
            return cast(dict[str, Any], json.loads(data))
    except Exception as e:
        logger.warning(
            "get_conversation_state_error",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )
    return None


async def clear_conversation_state(phone_number: str) -> None:
    try:
        redis_client = RedisClient.get_client()
        await redis_client.delete(_user_key(phone_number, "conversation_state"))
    except Exception as e:
        logger.error("error_clearing_state", phone_hash=log_fingerprint(phone_number), error_type=type(e).__name__)


async def get_last_response(phone_number: str) -> str | None:
    try:
        redis_client = RedisClient.get_client()
        return cast(str | None, await redis_client.get(_user_key(phone_number, "last_response")))
    except Exception as e:
        logger.warning(
            "get_last_response_error",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )
    return None


async def save_last_response(phone_number: str, response: str) -> None:
    try:
        redis_client = RedisClient.get_client()
        await redis_client.set(_user_key(phone_number, "last_response"), response, ex=3600)
    except Exception as e:
        logger.warning(
            "save_last_response_error",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )


async def save_message_id(phone_number: str, message_id: str) -> None:
    try:
        redis_client = RedisClient.get_client()
        await redis_client.set(_user_key(phone_number, "current_message_id"), message_id, ex=300)
    except Exception as e:
        logger.warning(
            "save_message_id_error",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )


async def claim_inbound_message(phone_number: str, message_id: str, ttl_seconds: int = 86400) -> bool:
    if not message_id or message_id == "unknown":
        return True
    try:
        redis_client = RedisClient.get_client()
        claimed = await redis_client.set(
            _user_key(phone_number, f"inbound_message:{message_id}"),
            "1",
            ex=ttl_seconds,
            nx=True,
        )
        return bool(claimed)
    except Exception as e:
        logger.warning(
            "claim_inbound_message_error",
            phone_hash=log_fingerprint(phone_number),
            message_id_hash=log_fingerprint(message_id),
            error_type=type(e).__name__,
        )
        return True


async def release_inbound_message_claim(phone_number: str, message_id: str) -> None:
    if not message_id or message_id == "unknown":
        return
    try:
        redis_client = RedisClient.get_client()
        await redis_client.delete(_user_key(phone_number, f"inbound_message:{message_id}"))
    except Exception as e:
        logger.warning(
            "release_inbound_message_error",
            phone_hash=log_fingerprint(phone_number),
            message_id_hash=log_fingerprint(message_id),
            error_type=type(e).__name__,
        )


async def get_message_id(phone_number: str) -> str | None:
    try:
        redis_client = RedisClient.get_client()
        return cast(str | None, await redis_client.get(_user_key(phone_number, "current_message_id")))
    except Exception as e:
        logger.warning(
            "get_message_id_error",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )
        return None


async def get_conversation_history(phone_number: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        redis_client = RedisClient.get_client()
        items = await cast(
            Awaitable[list[str]],
            redis_client.lrange(_user_key(phone_number, "chat_history"), -limit, -1),
        )
        return [json.loads(item) for item in items]
    except Exception as e:
        logger.warning(
            "get_conversation_history_error",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )
        return []


async def add_conversation_turn(
    phone_number: str,
    role: str,
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        redis_client = RedisClient.get_client()
        key = _user_key(phone_number, "chat_history")
        payload: dict[str, Any] = {"role": role, "content": content}
        clean_metadata = {key: value for key, value in (metadata or {}).items() if value is not None}
        if clean_metadata:
            payload["metadata"] = clean_metadata
            topic = clean_metadata.get("topic")
            if isinstance(topic, str) and topic.strip():
                payload["topic"] = topic.strip()
        message = json.dumps(payload)
        await cast(Awaitable[Any], redis_client.rpush(key, message))
        await cast(Awaitable[Any], redis_client.ltrim(key, -50, -1))
        await cast(Awaitable[Any], redis_client.expire(key, 86400))
    except Exception as e:
        logger.warning(
            "add_conversation_turn_error",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )


async def get_mandate_warning_count(phone_number: str) -> int:
    try:
        redis_client = RedisClient.get_client()
        count = await redis_client.get(_user_key(phone_number, "mandate_warning_count"))
        return int(count) if count else 0
    except Exception as e:
        logger.error("get_mandate_warning_count_error", error=str(e))
        return 0


async def increment_mandate_warning_count(phone_number: str, ttl: int = 300) -> int:
    try:
        redis_client = RedisClient.get_client()
        key = _user_key(phone_number, "mandate_warning_count")
        count = await redis_client.incr(key)
        await redis_client.expire(key, ttl)
        return cast(int, count)
    except Exception as e:
        logger.error("increment_mandate_warning_count_error", error=str(e))
        return 1
