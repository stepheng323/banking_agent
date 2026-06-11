from typing import Any

from banking.accounts.mandate_state import effective_mandate_status
from banking.presentation.formatters.accounts import get_bank_label, get_last4
from banking.presentation.i18n.renderer import render_message
from shared.money import MoneyAmount

STATUS_ICONS = {
    "ready": "✓",
    "pending": "○",
    "awaiting_authorization": "○",
    "approved": "○",
    "expired": "!",
    "cancelled": "!",
    "paused": "!",
    "rejected": "!",
    None: "",
}


class AccountFormatter:
    @staticmethod
    def _mask_from_last4(last4: str | None) -> str:
        """Build a markdown-safe masked account suffix from a known last4 value."""
        suffix = (last4 or "").strip() or "????"
        return f"···{suffix}"

    @staticmethod
    def format_account_list(accounts: list[Any], locale: str = "en") -> str:
        """Format list of accounts for display."""
        if not accounts:
            return render_message("account.list.empty", locale)

        lines = [render_message("account.list.header", locale), ""]

        for i, account in enumerate(accounts, 1):
            bank_name = get_bank_label(account)
            last4 = get_last4(account)

            if isinstance(account, dict):
                is_default = account.get("is_default", False)
            else:
                is_default = getattr(account, "is_default", False)

            default_badge = render_message("account.list.default_badge", locale) if is_default else ""
            effective_status = effective_mandate_status(account)
            status_icon = STATUS_ICONS.get(effective_status, "")
            status_suffix = f" [{status_icon}]" if status_icon else ""
            masked = AccountFormatter._mask_from_last4(last4)

            lines.append(f"{i}. {bank_name} ({masked}){default_badge}{status_suffix}")

        lines.append("")
        lines.append(render_message("account.list.commands_hint", locale))

        return "\n".join(lines)

    @staticmethod
    def _mask_account(account_number: str | None) -> str:
        """Build markdown-safe masked account suffix."""
        last4 = account_number[-4:] if account_number else "????"
        return AccountFormatter._mask_from_last4(last4)

    @staticmethod
    def format_balance_response(balances: list[dict], total_balance: MoneyAmount | None, locale: str = "en") -> str:
        """Format balance check response as natural language."""
        if not balances:
            return render_message("account.balance.none_available", locale)

        if len(balances) == 1:
            bal = balances[0]
            masked = AccountFormatter._mask_account(bal.get("account_number"))
            return render_message(
                "account.balance.natural_single",
                locale,
                {
                    "bank_name": bal["bank_name"],
                    "masked": masked,
                    "amount": f"{bal['amount']:,.2f}",
                },
            )

        lines = [render_message("account.balance.natural_multi_intro", locale), ""]

        for bal in balances:
            masked = AccountFormatter._mask_account(bal.get("account_number"))
            lines.append(
                render_message(
                    "account.balance.natural_multi_item",
                    locale,
                    {
                        "bank_name": bal["bank_name"],
                        "masked": masked,
                        "amount": f"{bal['amount']:,.2f}",
                    },
                )
            )

        if total_balance is not None:
            lines.append("")
            lines.append(render_message("account.balance.total", locale, {"total_balance": f"{total_balance:,.2f}"}))

        return "\n".join(lines)
