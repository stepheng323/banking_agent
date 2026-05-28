from typing import Any

from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from shared.config.settings import settings


def _pending_transaction_interrupt_expiry(interrupt: Any, current_task_types: set[str]) -> float | None:
    if not current_task_types.intersection(TRANSACTION_INTENTS):
        return None
    ttl_seconds = max(int(getattr(settings, "pending_transaction_ttl", 0) or 0), 0)
    if ttl_seconds <= 0:
        return None
    explicit_expiry = getattr(interrupt, "expires_at_ts", None)
    if isinstance(explicit_expiry, (int, float)) and explicit_expiry > 0:
        return float(explicit_expiry)
    created_at = getattr(interrupt, "created_at_ts", None)
    if not isinstance(created_at, (int, float)) or created_at <= 0:
        return None
    return float(created_at) + ttl_seconds


__all__ = ["_pending_transaction_interrupt_expiry"]
