"""Verification data service for flow token storage."""

import json
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)

VERIFICATION_STORAGE_TTL = 3600  # 1 hour TTL for verification data


async def get_verification_data(flow_token: str) -> dict[str, Any]:
    """Get verification data from Redis for a given flow_token."""
    if not flow_token:
        return {}

    redis_client = RedisClient.get_client()
    try:
        data = await redis_client.get(f"verification:{flow_token}")
        if data:
            return json.loads(data)
        return {}
    except Exception as e:
        logger.warning("verification_data_read_error", error=str(e))
        return {}


async def set_verification_data(
    flow_token: str,
    data: dict[str, Any],
    ttl: int = VERIFICATION_STORAGE_TTL,
) -> None:
    """Set verification data in Redis for a given flow_token."""
    if not flow_token:
        return

    redis_client = RedisClient.get_client()
    try:
        await redis_client.set(
            f"verification:{flow_token}",
            json.dumps(data),
            ex=ttl,
        )
    except Exception as e:
        logger.warning("verification_data_write_error", error=str(e))


async def update_verification_data(
    flow_token: str,
    updates: dict[str, Any],
    ttl: int = VERIFICATION_STORAGE_TTL,
) -> None:
    """Update verification data in Redis, merging with existing data."""
    if not flow_token:
        return

    existing = await get_verification_data(flow_token)
    existing.update(updates)
    await set_verification_data(flow_token, existing, ttl)
