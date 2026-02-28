"""Utility functions for receipt generation."""

from decimal import Decimal
from typing import Any


def mask_account(account: str) -> str:
    """Mask account number showing only last 4 digits.

    Args:
        account: Full account number

    Returns:
        Masked account (e.g., "1234567890" -> "****7890")
    """
    if not account:
        return "****"
    account = str(account).strip()
    if len(account) <= 4:
        return account
    return "****" + account[-4:]


def format_naira(amount: Decimal | float | int) -> str:
    """Format amount as Nigerian Naira.

    Args:
        amount: Amount to format

    Returns:
        Formatted string (e.g., "₦1,234.56")
    """
    amount = Decimal(str(amount))
    return f"₦{amount:,.2f}"


def format_datetime(dt: Any) -> str:
    """Format datetime for receipt display.

    Args:
        dt: datetime object

    Returns:
        Formatted string (e.g., "02 Jan 2026, 14:30")
    """
    return str(dt.strftime("%d %b %Y, %H:%M"))
