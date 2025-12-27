"""Global flow session management for all transaction types."""

import time

from shared.cache.redis_client import RedisClient
from shared.config import settings


async def get_flow_session_age(phone_number: str, flow_type: str) -> float | None:
    """
    Get the age of the current flow session in seconds, or None if no active session.

    Args:
        phone_number: User's phone number
        flow_type: Type of flow (e.g., "transfer", "airtime")

    Returns:
        Session age in seconds, or None if no active session
    """
    try:
        redis_client = RedisClient.get_client()
        key = f"user:{phone_number}:{flow_type}_session_start"
        session_start = await redis_client.get(key)
        if session_start:
            start_time = float(session_start)
            age = time.time() - start_time
            return age
    except Exception as e:
        print(f"⚠️  Error getting {flow_type} session age: {e}")
    return None


async def start_flow_session(phone_number: str, flow_type: str) -> None:
    """
    Start a new flow session by storing the current timestamp.

    Args:
        phone_number: User's phone number
        flow_type: Type of flow (e.g., "transfer", "airtime")
    """
    try:
        redis_client = RedisClient.get_client()
        key = f"user:{phone_number}:{flow_type}_session_start"
        await redis_client.set(key, str(time.time()), ex=settings.flow_session_timeout)
        print(f"🕐 Started {flow_type} session for {phone_number}")
    except Exception as e:
        print(f"⚠️  Error starting {flow_type} session: {e}")


async def clear_flow_session(phone_number: str, flow_type: str) -> None:
    """
    Clear the flow session.

    Args:
        phone_number: User's phone number
        flow_type: Type of flow (e.g., "transfer", "airtime")
    """
    try:
        redis_client = RedisClient.get_client()
        key = f"user:{phone_number}:{flow_type}_session_start"
        await redis_client.delete(key)
        print(f"🧹 Cleared {flow_type} session for {phone_number}")
    except Exception as e:
        print(f"⚠️  Error clearing {flow_type} session: {e}")
