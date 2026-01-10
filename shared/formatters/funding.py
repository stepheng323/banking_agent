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
    
    lines = []
    
    # We'll drop the transaction summary header (lines 45-53) and integrate it into the text as per Option B design.
    # Logic:
    
    lines.append("*Insufficient Funds*")
    lines.append("")
    
    recipient_display = recipient_name.title() if recipient_name else "Recipient"
    transfer_amount_str = _format_currency_naira(transfer_amount)
    balance_str = _format_currency_naira(available_balance)
    
    lines.append(
        f"You're trying to send *{transfer_amount_str}* to {recipient_display}, "
        f"but your *{bank_name}* balance is only *{balance_str}*."
    )
    lines.append("")
    
    lines.append("*Options:*")
    lines.append(f"• Send *{_format_currency_naira(max_available)}* instead")
    lines.append("• Add funds & retry")
    lines.append("• Cancel transaction")

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
