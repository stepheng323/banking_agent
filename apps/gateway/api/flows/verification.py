"""Verification data utilities for flow webhook."""

import json
from typing import Any, Dict

from shared.cache.redis_client import RedisClient


VERIFICATION_STORAGE_TTL = 3600  # 1 hour TTL for verification data


async def get_verification_data(flow_token: str) -> Dict[str, Any]:
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
        print(f"⚠️  Error reading verification data from Redis: {e}")
        return {}


async def set_verification_data(
    flow_token: str,
    data: Dict[str, Any],
    ttl: int = VERIFICATION_STORAGE_TTL
) -> None:
    """Set verification data in Redis for a given flow_token."""
    if not flow_token:
        return

    redis_client = RedisClient.get_client()
    try:
        await redis_client.set(
            f"verification:{flow_token}",
            json.dumps(data),
            ex=ttl
        )
    except Exception as e:
        print(f"⚠️  Error writing verification data to Redis: {e}")


async def update_verification_data(
    flow_token: str,
    updates: Dict[str, Any],
    ttl: int = VERIFICATION_STORAGE_TTL
) -> None:
    """Update verification data in Redis, merging with existing data."""
    if not flow_token:
        return

    existing = await get_verification_data(flow_token)
    existing.update(updates)
    await set_verification_data(flow_token, existing, ttl)

