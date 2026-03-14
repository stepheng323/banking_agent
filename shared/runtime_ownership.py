"""Runtime ownership metadata for migration-aware health and startup logs."""

from __future__ import annotations

from typing import Any

from shared.config.settings import settings


def build_runtime_status(runtime_name: str) -> dict[str, Any]:
    """Build a health/log payload describing runtime ownership flags."""
    status: dict[str, Any] = {
        "runtime_name": runtime_name,
        "stack_role": settings.runtime_stack_role,
        "app_env": settings.app_env,
        "environment": settings.environment,
        "chat_transport": settings.chat_transport,
        "async_transport": settings.async_transport,
        "enable_webhook_ingress": settings.enable_webhook_ingress,
        "enable_chat_consumers": settings.enable_chat_consumers,
        "enable_transaction_worker": settings.enable_transaction_worker,
        "enable_funding_worker": settings.enable_funding_worker,
        "enable_payout_worker": settings.enable_payout_worker,
        "enable_refund_worker": settings.enable_refund_worker,
        "enable_receipt_worker": settings.enable_receipt_worker,
        "enable_outbound_sender": settings.enable_outbound_sender,
        "is_passive_runtime": settings.is_passive_runtime,
    }
    return status
