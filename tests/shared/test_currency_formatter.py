from banking.presentation.formatters.currency import (
    coerce_amount,
    format_amount_number,
    format_naira,
    format_naira_compact,
)


def test_format_naira_defaults_to_whole_naira() -> None:
    assert format_naira(12345.67) == "₦12,346"


def test_format_naira_can_keep_fixed_decimals() -> None:
    assert format_naira(12345, decimal_places=2) == "₦12,345.00"


def test_format_naira_compact_keeps_fraction_only_when_needed() -> None:
    assert format_naira_compact(12345) == "₦12,345"
    assert format_naira_compact(12345.5) == "₦12,345.5"


def test_format_amount_number_has_no_currency_symbol() -> None:
    assert format_amount_number(12345.67, decimal_places=2) == "12,345.67"


def test_currency_helpers_use_deterministic_fallbacks() -> None:
    assert coerce_amount("not-money") == 0
    assert format_naira("not-money") == "₦0"
    assert format_naira_compact(None) == "₦0"
