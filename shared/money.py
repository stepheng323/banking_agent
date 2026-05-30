"""Canonical helpers for monetary amounts.

Monetary amounts are represented internally as Decimal, quantized to the
smallest NGN display unit used by the app. JSON/provider boundaries should
convert through these helpers instead of passing floats through transactional
code.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TypeAlias

MoneyAmount: TypeAlias = Decimal

MONEY_QUANT = Decimal("0.01")
MINOR_UNITS_PER_NAIRA = Decimal("100")


def to_money(value: object) -> MoneyAmount | None:
    """Convert a supported amount-like value to quantized Decimal."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return quantize_money(value)
    if isinstance(value, int):
        return quantize_money(Decimal(value))
    if isinstance(value, float):
        return quantize_money(Decimal(str(value)))
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return quantize_money(Decimal(cleaned))
        except InvalidOperation:
            return None
    return None


def require_money(value: object) -> MoneyAmount:
    """Convert value to MoneyAmount or raise ValueError when invalid."""
    amount = to_money(value)
    if amount is None:
        raise ValueError("Invalid money amount")
    return amount


def quantize_money(value: Decimal) -> MoneyAmount:
    """Quantize a Decimal money value to two fractional digits."""
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def money_to_json(value: object) -> str | None:
    """Serialize money for JSON payloads without losing precision."""
    amount = to_money(value)
    return None if amount is None else format(amount, "f")


def money_to_provider_value(value: object) -> int | str | None:
    """Serialize money for provider JSON without using float."""
    amount = to_money(value)
    if amount is None:
        return None
    integral = amount.to_integral_value()
    if amount == integral:
        return int(integral)
    return format(amount, "f")


def to_minor_units(value: object) -> int:
    """Convert a money value to minor units, e.g. NGN to kobo."""
    amount = require_money(value)
    return int((amount * MINOR_UNITS_PER_NAIRA).to_integral_value(rounding=ROUND_HALF_UP))
