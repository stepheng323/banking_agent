"""Value coercion helpers for interrupt switch extraction."""

from typing import Any

from shared.money import MoneyAmount, to_naira


def _coerce_money(value: Any) -> MoneyAmount | None:
    return to_naira(value)


__all__ = ["_coerce_money"]
