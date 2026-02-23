"""Account formatting utilities for user-facing messages."""

from typing import Any

from shared.i18n import render_message


def get_last4(account: Any, locale: str = "en") -> str:
    """Get last 4 digits of account number."""
    fallback_last4 = render_message("source_account.list.last4_fallback", locale)
    if isinstance(account, dict):
        number = str(account.get("account_number") or account.get("number") or "")
        if number:
            return number[-4:]

        last4 = account.get("last4")
        if last4:
            return str(last4)

        acc_id = str(account.get("id") or "")
        return acc_id[-4:] if acc_id else fallback_last4
    else:
        number = str(getattr(account, "account_number", "") or getattr(account, "number", "") or "")
        if number:
            return number[-4:]

        last4 = getattr(account, "last4", None)
        if last4:
            return str(last4)

        acc_id = str(getattr(account, "id", "") or getattr(account, "account_id", "") or "")
        return acc_id[-4:] if acc_id else fallback_last4


def get_bank_label(account: Any, locale: str = "en") -> str:
    """Get bank label (name or type)."""
    fallback = render_message("source_account.list.bank_label_fallback", locale)
    if isinstance(account, dict):
        return str(
            account.get("bank_name") or account.get("bank") or account.get("name") or account.get("type") or fallback
        )
    else:
        return str(
            getattr(account, "bank_name", "")
            or getattr(account, "bank", "")
            or getattr(account, "name", "")
            or getattr(account, "type", "")
            or fallback
        )


def format_accounts_list(accounts: list[Any] | None, locale: str = "en") -> str:
    """Format a list of accounts into a string."""
    if not accounts:
        return render_message("source_account.no_accounts_found", locale)

    lines: list[str] = []
    for idx, account in enumerate(accounts):
        last4 = get_last4(account, locale=locale)
        bank = get_bank_label(account, locale=locale)
        lines.append(
            render_message(
                "source_account.list.item",
                locale,
                {"index": idx + 1, "bank": bank, "last4": last4},
            )
        )

    return "\n".join(lines)
