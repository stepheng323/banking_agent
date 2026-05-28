"""Helpers for safe user-facing error messages."""

from __future__ import annotations

from shared.i18n.renderer import render_message

_TECHNICAL_ERROR_MARKERS = (
    "traceback",
    "sqlalchemy",
    "asyncpg",
    "integrityerror",
    "notnullviolation",
    "session's transaction",
    "[sql:",
    "[parameters:",
    "pydantic",
    "validationerror",
    "keyerror",
    "attributeerror",
    "nonetype",
    "object has no attribute",
    "password",
    "secret",
    "token",
    "stack trace",
)


def looks_technical_error(message: str | None) -> bool:
    """Return True when a message looks like an internal exception, not safe copy."""

    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _TECHNICAL_ERROR_MARKERS)


def safe_user_error_message(message: str | None, *, task_type: str | None = None, locale: str = "en") -> str:
    """Return a user-safe failure message, preserving only non-technical copy."""

    cleaned = str(message or "").strip()
    if cleaned and not looks_technical_error(cleaned):
        return cleaned

    normalized_task_type = str(task_type or "").strip().lower()
    if normalized_task_type == "transfer":
        return render_message("transfer.error.execution_failed", locale)
    if normalized_task_type == "airtime":
        return render_message("airtime.error.execution_failed", locale)
    if normalized_task_type == "data":
        return render_message("data.error.execution_failed", locale)
    if normalized_task_type == "query":
        return render_message("query.error.execution_failed", locale)
    return render_message("query.error.execution_failed", locale)


__all__ = ["looks_technical_error", "safe_user_error_message"]

