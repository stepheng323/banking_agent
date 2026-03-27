"""Datetime helpers for explicit UTC handling."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(UTC)


def utc_now_naive() -> datetime:
    """Return the current UTC datetime without tzinfo for naive storage paths."""
    return utc_now().replace(tzinfo=None)
