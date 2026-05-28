"""WhatsApp typing indicator coordination."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

SendTypingIndicator = Callable[[str], Awaitable[dict[str, Any]]]


async def resolve_current_message_id(*, to: str, message_id: str | None) -> str | None:
    if message_id:
        logger.debug("whatsapp_message_id_provided", message_id_hash=log_fingerprint(message_id))
        return message_id

    try:
        redis_client = RedisClient.get_client()
        fetched_id = await redis_client.get(f"user:{to}:current_message_id")
        logger.debug(
            "whatsapp_message_id_lookup",
            to_hash=log_fingerprint(to),
            found=bool(fetched_id),
            message_id_hash=log_fingerprint(fetched_id),
        )
        return fetched_id
    except Exception as e:
        logger.warning(
            "whatsapp_message_id_lookup_failed",
            to_hash=log_fingerprint(to),
            error_type=type(e).__name__,
        )
        return None


async def maybe_send_typing_indicator(
    *,
    to: str,
    message_id: str | None,
    suppress_typing_indicator: bool,
    send_typing_indicator: SendTypingIndicator,
) -> str | None:
    if suppress_typing_indicator:
        return message_id

    resolved_message_id = await resolve_current_message_id(to=to, message_id=message_id)
    if not resolved_message_id:
        return None

    await send_typing_indicator(resolved_message_id)
    delay_seconds = max(0.0, settings.whatsapp.typing_indicator_delay_ms / 1000)
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    return resolved_message_id
