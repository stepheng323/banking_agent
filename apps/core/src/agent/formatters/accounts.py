"""Account formatting utilities for user-facing messages."""

from typing import Dict, List, Optional


def _get_last4(account: Dict) -> str:
    number = (
        str(account.get("account_number") or account.get("number") or "")
    )
    if number:
        return number[-4:]
    last4 = account.get("last4")
    if last4:
        return str(last4)
    # Fallback to id hash tail if nothing else
    acc_id = str(account.get("id") or "")
    return acc_id[-4:] if acc_id else "????"


def _get_bank_label(account: Dict) -> str:
    return (
        str(
            account.get("bank_name")
            or account.get("bank")
            or account.get("name")
            or account.get("type")
            or "Account"
        )
    )


def format_accounts_list(accounts: Optional[List[Dict]]) -> str:
    """Format a list of accounts into a string."""
    if not accounts:
        return "No accounts found."

    lines: List[str] = ["*Which account would you like to use?*", ""]
    for idx, account in enumerate(accounts):
        last4 = _get_last4(account)
        bank = _get_bank_label(account)
        masked = f"(...{last4})"
        lines.append(f"{idx + 1} *{bank}* {masked}")

    return "\n".join(lines)
