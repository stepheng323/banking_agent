"""Runtime readiness checks."""

from __future__ import annotations

from inspect import isawaitable
from typing import Any

from sqlalchemy import text

from shared.cache.redis_client import RedisClient
from shared.database.connection import get_engine
from shared.observability.events import emit_operational_event


async def check_database() -> dict[str, Any]:
    """Return a DB readiness check payload."""
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        emit_operational_event(
            "readiness_database_failed",
            severity="high",
            domain="readiness",
            details={"error_type": type(exc).__name__},
        )
        return {"status": "failed", "error_type": type(exc).__name__}


async def check_redis() -> dict[str, Any]:
    """Return a Redis readiness check payload."""
    try:
        client = RedisClient.get_client()
        result = client.ping()
        if isawaitable(result):
            await result
        return {"status": "ready"}
    except Exception as exc:
        emit_operational_event(
            "readiness_redis_failed",
            severity="high",
            domain="readiness",
            details={"error_type": type(exc).__name__},
        )
        return {"status": "failed", "error_type": type(exc).__name__}


async def dependency_readiness(*, require_db: bool = True, require_redis: bool = True) -> dict[str, Any]:
    """Return readiness checks and aggregate ready/failed status."""
    checks: dict[str, Any] = {}
    if require_db:
        checks["db"] = await check_database()
    if require_redis:
        checks["redis"] = await check_redis()
    ready = all(check.get("status") == "ready" for check in checks.values())
    return {"status": "ready" if ready else "not_ready", "checks": checks}
