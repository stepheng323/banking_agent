from typing import Any

from shared.i18n import render_message
from shared.formatters.accounts import get_bank_label, get_last4

STATUS_ICONS = {
    "ready": "✓",
    "pending": "○",
    "awaiting_authorization": "○",
    "expired": "!",
    "cancelled": "!",
    None: "",
}


class AccountFormatter:
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
                mandate_status = account.get("mandate_status")
            else:
                is_default = getattr(account, "is_default", False)
                mandate_status = getattr(account, "mandate_status", None)

            default_badge = render_message("account.list.default_badge", locale) if is_default else ""
            status_icon = STATUS_ICONS.get(mandate_status, "")
            status_suffix = f" [{status_icon}]" if status_icon else ""

            lines.append(f"{i}. {bank_name} (****{last4}){default_badge}{status_suffix}")

        lines.append("")
        lines.append(render_message("account.list.commands_hint", locale))

        return "\n".join(lines)

    @staticmethod
    def format_balance_response(balances: list[dict], total_balance: float | None, locale: str = "en") -> str:
        """Format balance check response."""
        if not balances:
            return render_message("account.balance.none_available", locale)

        header = (
            render_message("account.balance.header_single", locale)
            if len(balances) == 1
            else render_message("account.balance.header_multi", locale)
        )
        lines = [header, ""]

        for index, bal in enumerate(balances, 1):
            amount = bal["amount"]
            currency = bal.get("currency", "NGN")
            symbol = "₦" if currency == "NGN" else currency
            account_number = bal.get("account_number") or ""
            last4 = account_number[-4:] if account_number else "????"
            masked = f"****{last4}"

            lines.append(f"{index}. {bal['bank_name']} ({masked}): **{symbol}{amount:,.2f}**")

        if total_balance is not None:
            lines.append("")
            lines.append(render_message("account.balance.total", locale, {"total_balance": f"{total_balance:,.2f}"}))

        return "\n".join(lines)
