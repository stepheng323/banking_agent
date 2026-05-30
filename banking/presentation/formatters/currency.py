"""Currency formatting helpers for user-facing copy."""

from typing import Any


def coerce_amount(value: Any, *, default: float = 0.0, absolute: bool = False) -> float:
    """Convert an amount-like value to float with a deterministic fallback."""
    try:
        amount = float(value or default)
    except (TypeError, ValueError):
        amount = default
    return abs(amount) if absolute else amount


def format_amount_number(
    value: Any,
    *,
    decimal_places: int = 0,
    trim_trailing_decimals: bool = False,
    absolute: bool = False,
) -> str:
    """Format an amount as a grouped number without a currency symbol."""
    amount = coerce_amount(value, absolute=absolute)
    formatted = f"{amount:,.{decimal_places}f}"
    if trim_trailing_decimals and "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def format_naira(
    value: Any,
    *,
    decimal_places: int = 0,
    trim_trailing_decimals: bool = False,
    absolute: bool = False,
) -> str:
    """Format an amount with the Naira symbol."""
    amount = format_amount_number(
        value,
        decimal_places=decimal_places,
        trim_trailing_decimals=trim_trailing_decimals,
        absolute=absolute,
    )
    return f"₦{amount}"


def format_naira_compact(value: Any, *, absolute: bool = False) -> str:
    """Format Naira without forced decimals, preserving fractional values when present."""
    amount = coerce_amount(value, absolute=absolute)
    if amount.is_integer():
        return f"₦{amount:,.0f}"
    return format_naira(amount, decimal_places=2, trim_trailing_decimals=True)
