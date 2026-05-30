"""Common helpers for transfer formatters."""

from __future__ import annotations

from banking.presentation.formatters.currency import coerce_amount
from shared.money import MoneyAmount, quantize_money


def calculate_transfer_fee(amount: MoneyAmount) -> MoneyAmount:
    amount_value = coerce_amount(amount)
    fee = quantize_money(amount_value * coerce_amount("0.005"))
    return max(fee, coerce_amount("10"))


def resolve_display_narration(data: dict) -> str | None:
    for key in ("authored_narration", "user_note"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
