"""Funding-related message formatting utilities."""

from typing import Any


def _format_currency_naira(amount: float) -> str:
    """Format amount as Naira currency."""
    try:
        value = float(amount)
    except Exception:
        return f"₦{amount}"
    return f"₦{value:,.0f}"


def format_insufficient_funds(
    transfer_amount: float,
    bank_name: str,
    available_balance: float,
    max_available: float = 0,
    recipient_name: str = "",
    recipient_bank: str = "",
    recipient_account: str = "",
) -> str:
    """Format insufficient funds message.

    Args:
        transfer_amount: Amount user is trying to send
        bank_name: Name of the primary bank account
        available_balance: Current available balance in primary account
        max_available: Maximum available across all accounts (defaults to available_balance)
        recipient_name: Name of the recipient
        recipient_bank: Recipient's bank name
        recipient_account: Recipient's account number

    Returns:
        WhatsApp-formatted error message
    """
    # If max_available not specified, use available_balance
    if max_available <= 0:
        max_available = available_balance

    lines = []

    # Header with recipient details if provided
    if recipient_name and recipient_bank:
        recipient_display = recipient_name.title()
        lines.append(
            f"*{_format_currency_naira(transfer_amount)} → {recipient_display} ({recipient_bank})*"
        )
        if recipient_account:
            lines.append(f"Account: {recipient_account}")
        lines.append("")

    # Insufficient funds notice
    lines.append("*Insufficient funds*")
    lines.append("")
    balance_str = _format_currency_naira(available_balance)
    lines.append(f"Your {bank_name} balance is *{balance_str}* — not enough for this transfer.")
    lines.append("")
    lines.append("You can:")
    lines.append(f"• Send a smaller amount (up to {_format_currency_naira(max_available)})")
    lines.append("• Add funds to your account")
    lines.append("• Cancel this transfer")

    return "\n".join(lines)


def format_funding_plan_message(
    transfer_amount: float,
    steps: list[dict[str, Any]],
) -> str:
    """Format a funding plan message showing how transfer will be funded.

    Args:
        transfer_amount: Total transfer amount
        steps: List of funding steps with bank_name and amount

    Returns:
        WhatsApp-formatted funding plan message
    """
    if len(steps) == 1:
        step = steps[0]
        amount_str = _format_currency_naira(transfer_amount)
        bank = step.get("bank_name", "account")
        return f"{amount_str} will be debited from your {bank} account."

    lines = [f"To send {_format_currency_naira(transfer_amount)}, I'll combine:"]
    for step in steps:
        bank = step.get("bank_name", "Account")
        amount = float(step.get("amount", 0))
        lines.append(f"• {_format_currency_naira(amount)} from {bank}")

    return "\n".join(lines)
