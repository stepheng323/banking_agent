"""Thread-level invocation locking for orchestrator graph turns."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from shared.cache.distributed_lock import RedisDistributedLock
from shared.config.settings import settings


@asynccontextmanager
async def thread_invocation_lock(redis_client: Any, thread_id: str, *, logger: Any) -> AsyncIterator[None]:
    lock = RedisDistributedLock(
        redis_client,
        key=f"chat:thread-lock:{thread_id}",
        ttl_seconds=settings.chat_thread_lock_ttl_seconds,
    )
    await lock.acquire(wait_seconds=settings.chat_thread_lock_wait_seconds)
    renew_task = asyncio.create_task(
        lock.renew_periodically(interval_seconds=settings.chat_thread_lock_renew_seconds),
        name=f"chat-thread-lock-renew:{thread_id}",
    )
    try:
        logger.info(
            "orchestrator_thread_lock_acquired",
            thread_id=thread_id,
            ttl_seconds=settings.chat_thread_lock_ttl_seconds,
            renew_seconds=settings.chat_thread_lock_renew_seconds,
        )
        yield
    finally:
        renew_task.cancel()
        renew_results = await asyncio.gather(renew_task, return_exceptions=True)
        for result in renew_results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.warning("orchestrator_thread_lock_renew_failed", thread_id=thread_id, error=str(result))
        try:
            released = await lock.release()
        except Exception as exc:
            logger.warning("orchestrator_thread_lock_release_failed", thread_id=thread_id, error=str(exc))
        else:
            logger.info("orchestrator_thread_lock_released", thread_id=thread_id, released=released)
