"""Amount validation utilities for transaction security.

Provides centralized validation for all transaction amounts
to prevent negative, zero, or out-of-bounds values from being processed.
"""

from dataclasses import dataclass

from shared.i18n import render_message


@dataclass(frozen=True)
class AmountLimits:
    """Transaction amount limits configuration."""

    min_amount: float
    max_amount: float
    transaction_type: str


# Transaction-specific limits
TRANSFER_LIMITS = AmountLimits(
    min_amount=100.0,
    max_amount=10_000_000.0,
    transaction_type="transfer",
)

AIRTIME_LIMITS = AmountLimits(
    min_amount=50.0,
    max_amount=50_000.0,
    transaction_type="airtime",
)

DATA_LIMITS = AmountLimits(
    min_amount=50.0,
    max_amount=50_000.0,
    transaction_type="data",
)


def validate_amount(
    amount: float | int | str | None,
    limits: AmountLimits,
    locale: str = "en",
) -> tuple[bool, str | None, float | None]:
    """
    Validate transaction amount against limits.

    Args:
        amount: The amount to validate (can be float, int, or string)
        limits: The AmountLimits configuration for this transaction type

    Returns:
        Tuple of (is_valid, error_message, validated_amount)
        - is_valid: True if amount passes all checks
        - error_message: Human-readable error message if invalid, None otherwise
        - validated_amount: The amount as float if valid, None otherwise
    """
    if amount is None:
        return False, render_message("validation.amount.required", locale), None

    try:
        amount_float = float(amount)
    except (ValueError, TypeError):
        return False, render_message("validation.amount.invalid_format", locale), None

    if amount_float <= 0:
        return False, render_message("validation.amount.gt_zero", locale), None

    if amount_float < limits.min_amount:
        return (
            False,
            render_message(
                "validation.amount.minimum",
                locale,
                {"transaction_type": limits.transaction_type, "amount": f"{limits.min_amount:,.0f}"},
            ),
            None,
        )

    if amount_float > limits.max_amount:
        return (
            False,
            render_message(
                "validation.amount.maximum",
                locale,
                {"transaction_type": limits.transaction_type, "amount": f"{limits.max_amount:,.0f}"},
            ),
            None,
        )

    return True, None, amount_float


def validate_percentage(
    percentage: float | int | str | None,
    locale: str = "en",
) -> tuple[bool, str | None, float | None]:
    """
    Validate transfer percentage (0-100 exclusive of 0, inclusive of 100).

    Used for percentage-based transfers like "send half" (50%) or "tithe" (10%).

    Args:
        percentage: The percentage to validate

    Returns:
        Tuple of (is_valid, error_message, validated_percentage)
        - is_valid: True if percentage passes all checks
        - error_message: Human-readable error message if invalid, None otherwise
        - validated_percentage: The percentage as float if valid, None otherwise
    """
    if percentage is None:
        return True, None, None  # Percentage is optional

    try:
        pct = float(percentage)
    except (ValueError, TypeError):
        return False, render_message("validation.percentage.invalid_format", locale), None

    if pct <= 0:
        return False, render_message("validation.percentage.gt_zero", locale), None

    if pct > 100:
        return False, render_message("validation.percentage.max_100", locale), None

    return True, None, pct


def validate_amount_basic(amount: float | int | str | None, locale: str = "en") -> tuple[bool, str | None]:
    """
    Basic amount validation without transaction-specific limits.

    Used for defense-in-depth checks where we just need to ensure
    the amount is a valid positive number.

    Args:
        amount: The amount to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if amount is None:
        return False, render_message("validation.amount.required", locale)

    try:
        amount_float = float(amount)
    except (ValueError, TypeError):
        return False, render_message("validation.amount.invalid_format_short", locale)

    if amount_float <= 0:
        return False, render_message("validation.amount.gt_zero", locale)

    return True, None
