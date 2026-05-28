"""Common helpers for transfer formatters."""

from __future__ import annotations

from shared.formatters.currency import coerce_amount


def calculate_transfer_fee(amount: float) -> float:
    amount_value = coerce_amount(amount)
    fee = round(amount_value * 0.005)
    return float(max(fee, 10))


def resolve_display_narration(data: dict) -> str | None:
    for key in ("authored_narration", "user_note"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
