"""Safe identifier coercion for funding plans.

Funding plans eventually cross a database boundary where account IDs are UUIDs.
Checkpoint and demo payloads are user/session data, however, and must never be
allowed to turn a malformed identifier into a raw ``UUID`` exception.
"""

from __future__ import annotations

from uuid import UUID


def coerce_account_uuid(value: object) -> UUID | None:
    """Return a UUID for *value*, or ``None`` when it is not a valid UUID."""

    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return UUID(candidate)
    except (ValueError, TypeError, AttributeError):
        return None


def coerce_account_id(value: object) -> UUID | str | None:
    """Normalize an account identifier without throwing on checkpoint values.

    Demo fixtures and older checkpoints can use stable non-UUID account keys.
    They remain usable for in-memory matching; only a valid UUID is handed to
    database-specific code.
    """

    parsed = coerce_account_uuid(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = ["coerce_account_id", "coerce_account_uuid"]
