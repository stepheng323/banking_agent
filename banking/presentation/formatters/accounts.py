"""Account formatting utilities for user-facing messages."""

from typing import Any

from banking.presentation.i18n.renderer import render_message


def get_last4(account: Any, locale: str = "en") -> str:
    """Get last 4 digits of account number."""
    fallback_last4 = render_message("source_account.list.last4_fallback", locale)
    if isinstance(account, dict):
        number = str(account.get("account_number") or account.get("number") or "")
        if number:
            return number[-4:]

        last4 = account.get("last4") or account.get("account_number_last4")
        if last4:
            return str(last4)

        return fallback_last4
    else:
        number = str(getattr(account, "account_number", "") or getattr(account, "number", "") or "")
        if number:
            return number[-4:]

        last4 = getattr(account, "last4", None) or getattr(account, "account_number_last4", None)
        if last4:
            return str(last4)

        return fallback_last4


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


def format_source_account_info_line(
    bank: str,
    last4: str,
    *,
    locale: str = "en",
    balance: float | None = None,
) -> str:
    """Format the source account line with optional balance."""
    if balance is not None:
        return render_message(
            "orchestrator.execution.source_account_info_with_balance",
            locale,
            {"bank": bank, "last4": last4, "balance": f"{balance:,.2f}"},
        )
    return render_message(
        "orchestrator.execution.source_account_info",
        locale,
        {"bank": bank, "last4": last4},
    )


def format_source_account_info_from_account_number(
    bank: str,
    account_number: str | None,
    *,
    locale: str = "en",
    balance: float | None = None,
) -> str:
    """Format source account line from account number with consistent fallback."""
    last4_fallback = render_message("source_account.list.last4_fallback", locale)
    account_str = str(account_number or "")
    last4 = account_str[-4:] if account_str else last4_fallback
    return format_source_account_info_line(bank=bank, last4=last4, locale=locale, balance=balance)
