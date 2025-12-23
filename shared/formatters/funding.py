"""Funding-related message formatting utilities."""

from typing import List, Dict, Any


def _format_currency_naira(amount: float) -> str:
    """Format amount as Naira currency."""
    try:
        value = float(amount)
    except Exception:
        return f"₦{amount}"
    return f"₦{value:,.0f}"


def format_insufficient_funds_single_account(
    bank_name: str,
    available_balance: float,
    transfer_amount: float,
) -> str:
    """Format insufficient funds message for single account scenario.

    Args:
        bank_name: Name of the bank account
        available_balance: Current available balance
        transfer_amount: Amount user is trying to send

    Returns:
        WhatsApp-formatted error message
    """
    lines = [
        f"Your *{bank_name}* balance is *{_format_currency_naira(available_balance)}*.",
        "",
        f"This transfer needs *{_format_currency_naira(transfer_amount)}*.",
        "",
        "You can change the amount, add funds, or cancel the transfer.",
    ]
    return "\n".join(lines)


def format_insufficient_funds_multi_account(
    transfer_amount: float,
    total_available: float,
    shortfall: float,
    account_balances: List[Dict[str, Any]],
) -> str:
    """Format insufficient funds message for multi-account scenario.

    Args:
        transfer_amount: Amount user is trying to send
        total_available: Total available across all accounts
        shortfall: Amount still needed
        account_balances: List of dicts with bank_name and amount keys

    Returns:
        WhatsApp-formatted error message with account breakdown
    """
    lines = [
        f"This transfer needs *{_format_currency_naira(transfer_amount)}*.",
        "",
        f"Your total balance across {len(account_balances)} accounts is *{_format_currency_naira(total_available)}*.",
        "",
    ]

    if account_balances:
        lines.append("*Your balances:*")
        for account in account_balances:
            bank = account.get("bank_name", "Account")
            amount = float(account.get("amount", 0))
            lines.append(f"- {bank}: *{_format_currency_naira(amount)}*")
        lines.append("")

    lines.append("You can change the amount, add funds, or cancel the transfer.")

    return "\n".join(lines)


def format_funding_plan_message(
    transfer_amount: float,
    steps: List[Dict[str, Any]],
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
        return f"{_format_currency_naira(transfer_amount)} will be debited from your {step.get('bank_name', 'account')} account."

    lines = [f"To send {_format_currency_naira(transfer_amount)}, I'll combine:"]
    for step in steps:
        bank = step.get("bank_name", "Account")
        amount = float(step.get("amount", 0))
        lines.append(f"• {_format_currency_naira(amount)} from {bank}")

    return "\n".join(lines)
