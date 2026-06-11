"""Helpers for task-bound transaction authorization context."""

from __future__ import annotations

from typing import Any


def authorized_idempotency_keys(context: dict[str, Any]) -> set[str]:
    """Return idempotency keys covered by the verified PIN callback context."""
    raw_context = context.get("authorization_context")
    if not isinstance(raw_context, dict):
        return set()

    keys: set[str] = set()
    raw_key = raw_context.get("idempotency_key")
    if isinstance(raw_key, str) and raw_key.strip():
        keys.add(raw_key.strip())

    raw_keys = raw_context.get("authorized_task_idempotency_keys")
    if isinstance(raw_keys, list):
        keys.update(str(key).strip() for key in raw_keys if str(key).strip())
    return keys


def is_task_authorized_by_pin(
    *,
    context: dict[str, Any],
    idempotency_key: str | None,
    pin_verified: bool,
) -> bool:
    """Return true only when PIN verification is bound to the exact task key."""
    if not pin_verified:
        return False
    key = str(idempotency_key or "").strip()
    return bool(key and key in authorized_idempotency_keys(context))
