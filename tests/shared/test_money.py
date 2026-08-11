"""Tests for Decimal monetary amount helpers."""

from decimal import Decimal

import pytest

from shared.money import (
    kobo_to_naira,
    naira_to_kobo,
    naira_to_provider_value,
    require_naira,
    to_naira,
)


def test_money_addition_avoids_binary_float_rounding() -> None:
    total = require_naira("0.10") + require_naira("0.20")

    assert total == Decimal("0.30")


def test_float_input_converts_through_string_representation() -> None:
    assert require_naira(0.1) == Decimal("0.10")


def test_minor_units_are_exact() -> None:
    assert naira_to_kobo(Decimal("2000.05")) == 200005


def test_provider_value_uses_int_for_whole_amount_and_string_for_fractional_amount() -> None:
    assert naira_to_provider_value(Decimal("2000.00")) == 2000
    assert naira_to_provider_value(Decimal("2000.50")) == "2000.50"


def test_explicit_naira_and_kobo_helpers() -> None:
    assert require_naira("2000.05") == Decimal("2000.05")
    assert naira_to_kobo(Decimal("2000.05")) == 200005
    assert kobo_to_naira(200005) == Decimal("2000.05")


@pytest.mark.parametrize("value", ["NaN", "Infinity", True, float("nan"), float("inf"), "not-money"])
def test_invalid_naira_values_are_rejected(value: object) -> None:
    assert to_naira(value) is None
    with pytest.raises(ValueError):
        require_naira(value)
