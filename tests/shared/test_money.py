"""Tests for Decimal monetary amount helpers."""

from decimal import Decimal

from shared.money import money_to_provider_value, require_money, to_minor_units


def test_money_addition_avoids_binary_float_rounding() -> None:
    total = require_money("0.10") + require_money("0.20")

    assert total == Decimal("0.30")


def test_float_input_converts_through_string_representation() -> None:
    assert require_money(0.1) == Decimal("0.10")


def test_minor_units_are_exact() -> None:
    assert to_minor_units(Decimal("2000.05")) == 200005


def test_provider_value_uses_int_for_whole_amount_and_string_for_fractional_amount() -> None:
    assert money_to_provider_value(Decimal("2000.00")) == 2000
    assert money_to_provider_value(Decimal("2000.50")) == "2000.50"
