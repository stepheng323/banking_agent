"""Runtime metadata for health and startup logs."""

from __future__ import annotations

from typing import Any

from shared.config.settings import settings


def build_runtime_status(runtime_name: str) -> dict[str, Any]:
    """Build a health/log payload describing the running service."""
    return {
        "runtime_name": runtime_name,
        "app_env": settings.runtime.app_env,
        "environment": settings.runtime.infrastructure_environment,
        "chat_transport": settings.chat_transport,
        "async_transport": settings.async_transport,
    }
