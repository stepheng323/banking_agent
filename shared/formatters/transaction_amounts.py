"""Transaction amount display helpers."""

from __future__ import annotations

from shared.formatters.currency import format_naira


def format_amount(amount: float | int) -> str:
    """Format currency in Naira with fixed two-decimal precision."""
    return format_naira(amount, decimal_places=2)
