from typing import Any

from banking.accounts.mandate_state import effective_mandate_status
from banking.presentation.formatters.accounts import get_bank_label, get_last4
from banking.presentation.i18n.renderer import render_message
from shared.messaging.body_blocks import MessageDocument, render_body_blocks_text
from shared.money import MoneyAmount

STATUS_LABELS = {
    "ready": "Active",
    "pending": "Finish setup",
    "awaiting_authorization": "Finish setup",
    "approved": "Activating",
    "expired": "Unlinked",
    "cancelled": "Unlinked",
    "paused": "Paused",
    "rejected": "Unlinked",
    None: "",
}


class AccountFormatter:
    @staticmethod
    def _mask_from_last4(last4: str | None) -> str:
        """Build a markdown-safe masked account suffix from a known last4 value."""
        suffix = (last4 or "").strip() or "????"
        return f"···{suffix}"

    @staticmethod
    def format_account_list(accounts: list[Any], locale: str = "en", *, has_next: bool = False) -> str:
        """Format list of accounts for display."""
        return render_body_blocks_text(
            AccountFormatter.format_account_list_blocks(accounts, locale=locale, has_next=has_next)
        ) or (
            render_message("account.list.empty", locale)
        )

    @staticmethod
    def format_default_account(account: Any | None, locale: str = "en") -> str:
        """Format the current default account without exposing its full number."""
        if account is None:
            return render_message("account.default_unavailable", locale)
        return render_message(
            "account.default_identity",
            locale,
            {
                "bank_name": get_bank_label(account),
                "masked": AccountFormatter._mask_from_last4(get_last4(account)),
            },
        )

    @staticmethod
    def format_account_list_blocks(
        accounts: list[Any],
        locale: str = "en",
        *,
        has_next: bool = False,
    ) -> MessageDocument | None:
        """Format linked accounts as mobile-friendly message blocks."""
        if not accounts:
            return None

        blocks: MessageDocument = [
            {"type": "heading", "text": render_message("account.list.header", locale).strip("*")}
        ]
        action_hints: list[str] = []

        for i, account in enumerate(accounts, 1):
            bank_name = get_bank_label(account)
            last4 = get_last4(account)
            is_default = (
                account.get("is_default", False) if isinstance(account, dict) else getattr(account, "is_default", False)
            )
            default_label = "Default" if is_default else ""
            effective_status = effective_mandate_status(account)
            status_label = STATUS_LABELS.get(effective_status, "")
            masked = AccountFormatter._mask_from_last4(last4)
            detail_parts = [part for part in (default_label, status_label) if part]
            detail_line = " • ".join(detail_parts)
            title = f"{i}. {bank_name} ({masked})"
            if not detail_line:
                detail_line = "Linked"

            blocks.append(
                {
                    "type": "text",
                    "text": f"{title} — {detail_line}",
                }
            )

            if effective_status in {"expired", "cancelled", "rejected"} and len(action_hints) < 1:
                action_hints.append(f"link {bank_name}")

        if len(accounts) > 1:
            action_hints.append("set 2 as default")
        action_hints.append("unlink GTB")
        blocks.append({"type": "text", "text": f"Actions: {' | '.join(action_hints)}"})
        if has_next:
            blocks.append({"type": "text", "text": render_message("common.pagination.more", locale)})
        return blocks

    @staticmethod
    def _mask_account(account_number: str | None) -> str:
        """Build markdown-safe masked account suffix."""
        last4 = account_number[-4:] if account_number else "????"
        return AccountFormatter._mask_from_last4(last4)

    @staticmethod
    def format_balance_response(balances: list[dict], total_balance: MoneyAmount | None, locale: str = "en") -> str:
        """Format balance check response as natural language."""
        return render_body_blocks_text(
            AccountFormatter.format_balance_response_blocks(balances, total_balance, locale=locale)
        ) or render_message("account.balance.none_available", locale)

    @staticmethod
    def format_balance_response_blocks(
        balances: list[dict],
        total_balance: MoneyAmount | None,
        locale: str = "en",
    ) -> MessageDocument | None:
        """Format account balances as mobile-friendly message blocks."""
        if not balances:
            return None

        if len(balances) == 1:
            bal = balances[0]
            masked = AccountFormatter._mask_account(bal.get("account_number"))
            return [
                {
                    "type": "text",
                    "text": render_message(
                        "account.balance.natural_single",
                        locale,
                        {
                            "bank_name": bal["bank_name"],
                            "masked": masked,
                            "amount": f"{bal['amount']:,.2f}",
                        },
                    ),
                }
            ]

        blocks: MessageDocument = [
            {"type": "heading", "text": render_message("account.balance.header_multi", locale).strip("*")}
        ]
        for bal in balances:
            masked = AccountFormatter._mask_account(bal.get("account_number"))
            blocks.append(
                {
                    "type": "text",
                    "text": f"{bal['bank_name']} ({masked}): ₦{bal['amount']:,.2f}",
                }
            )

        if total_balance is not None:
            blocks.append(
                {
                    "type": "key_value",
                    "label": render_message("account.balance.total_label", locale),
                    "value": f"₦{total_balance:,.2f}",
                }
            )

        return blocks
