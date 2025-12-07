"""Account formatting utilities for user-facing messages."""

from typing import List, Optional, Any


def get_last4(account: Any) -> str:
    """Get last 4 digits of account number."""
    if isinstance(account, dict):
        number = str(account.get("account_number") or account.get("number") or "")
        if number:
            return number[-4:]
        
        last4 = account.get("last4")
        if last4:
            return str(last4)
            
        acc_id = str(account.get("id") or "")
        return acc_id[-4:] if acc_id else "????"
    else:
        number = str(getattr(account, "account_number", "") or getattr(account, "number", "") or "")
        if number:
            return number[-4:]
            
        last4 = getattr(account, "last4", None)
        if last4:
            return str(last4)
            
        acc_id = str(getattr(account, "id", "") or getattr(account, "account_id", "") or "")
        return acc_id[-4:] if acc_id else "????"


def get_bank_label(account: Any) -> str:
    """Get bank label (name or type)."""
    if isinstance(account, dict):
        return (
            str(
                account.get("bank_name")
                or account.get("bank")
                or account.get("name")
                or account.get("type")
                or "Account"
            )
        )
    else:
        return (
            str(
                getattr(account, "bank_name", "")
                or getattr(account, "bank", "")
                or getattr(account, "name", "")
                or getattr(account, "type", "")
                or "Account"
            )
        )


def format_accounts_list(accounts: Optional[List[Any]]) -> str:
    """Format a list of accounts into a string."""
    if not accounts:
        return "No accounts found."

    lines: List[str] = ["*Which account would you like to use?*", ""]
    for idx, account in enumerate(accounts):
        last4 = get_last4(account)
        bank = get_bank_label(account)
        masked = f"(...{last4})"
        lines.append(f"{idx + 1} *{bank}* {masked}")

    return "\n".join(lines)
