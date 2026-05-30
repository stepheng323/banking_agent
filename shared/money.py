"""Canonical helpers for monetary amounts.

Monetary amounts are represented internally as Decimal, quantized to the
smallest NGN display unit used by the app. JSON/provider boundaries should
convert through these helpers instead of passing floats through transactional
code.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from math import isfinite
from typing import TypeAlias

MoneyAmount: TypeAlias = Decimal

MONEY_QUANT = Decimal("0.01")
MINOR_UNITS_PER_NAIRA = Decimal("100")


def to_naira(value: object) -> MoneyAmount | None:
    """Convert a supported naira amount-like value to quantized Decimal."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return _quantize_naira_or_none(value)
    if isinstance(value, int):
        return _quantize_naira_or_none(Decimal(value))
    if isinstance(value, float):
        if not isfinite(value):
            return None
        return _quantize_naira_or_none(Decimal(str(value)))
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return _quantize_naira_or_none(Decimal(cleaned))
        except (InvalidOperation, ValueError):
            return None
    return None


def require_naira(value: object) -> MoneyAmount:
    """Convert value to naira MoneyAmount or raise ValueError when invalid."""
    amount = to_naira(value)
    if amount is None:
        raise ValueError("Invalid naira amount")
    return amount


def _quantize_naira_or_none(value: Decimal) -> MoneyAmount | None:
    if not value.is_finite():
        return None
    try:
        return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def quantize_naira(value: Decimal) -> MoneyAmount:
    """Quantize a Decimal naira value to two fractional digits."""
    amount = _quantize_naira_or_none(value)
    if amount is None:
        raise ValueError("Invalid naira amount")
    return amount


def naira_to_json(value: object) -> str | None:
    """Serialize naira for JSON payloads without losing precision."""
    amount = to_naira(value)
    return None if amount is None else format(amount, "f")


def naira_to_provider_value(value: object) -> int | str | None:
    """Serialize naira for provider JSON without using float."""
    amount = to_naira(value)
    if amount is None:
        return None
    integral = amount.to_integral_value()
    if amount == integral:
        return int(integral)
    return format(amount, "f")


def naira_to_kobo(value: object) -> int:
    """Convert a naira value to kobo."""
    amount = require_naira(value)
    return int((amount * MINOR_UNITS_PER_NAIRA).to_integral_value(rounding=ROUND_HALF_UP))


def kobo_to_naira(value: object) -> MoneyAmount | None:
    """Convert a kobo value to naira Decimal."""
    if value is None or isinstance(value, bool):
        return None
    try:
        kobo = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not kobo.is_finite():
        return None
    if kobo != kobo.to_integral_value():
        return None
    return quantize_naira(kobo / MINOR_UNITS_PER_NAIRA)


def require_kobo_to_naira(value: object) -> MoneyAmount:
    """Convert kobo to naira Decimal or raise ValueError when invalid."""
    amount = kobo_to_naira(value)
    if amount is None:
        raise ValueError("Invalid kobo amount")
    return amount


def to_money(value: object) -> MoneyAmount | None:
    """Backward-compatible alias for to_naira."""
    return to_naira(value)


def require_money(value: object) -> MoneyAmount:
    """Backward-compatible alias for require_naira."""
    return require_naira(value)


def quantize_money(value: Decimal) -> MoneyAmount:
    """Backward-compatible alias for quantize_naira."""
    return quantize_naira(value)


def money_to_json(value: object) -> str | None:
    """Backward-compatible alias for naira_to_json."""
    return naira_to_json(value)


def money_to_provider_value(value: object) -> int | str | None:
    """Backward-compatible alias for naira_to_provider_value."""
    return naira_to_provider_value(value)


def to_minor_units(value: object) -> int:
    """Backward-compatible alias for naira_to_kobo."""
    return naira_to_kobo(value)
