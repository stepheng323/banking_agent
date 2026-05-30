"""Funding plan summary formatting for transfer authorization."""

from __future__ import annotations

from banking.presentation.formatters.currency import coerce_amount, format_naira
from banking.presentation.i18n.renderer import render_message


def format_funding_plan_summary(
    steps: list[dict],
    amount: float,
    primary_bank: str,
    balance_available: float,
    recipient_name: str = "",
    recipient_bank: str = "",
    recipient_account: str = "",
    locale: str = "en",
) -> str:
    """Format funding plan summary for multi-account transfer authorization."""
    secondary_bank = None
    secondary_amount = 0.0
    for step in steps:
        if step.get("bank_name") != primary_bank:
            secondary_bank = step.get(
                "bank_name",
                render_message("transfer.format.funding_plan.secondary_bank_fallback", locale),
            )
            secondary_amount = coerce_amount(step.get("amount"))
            break

    lines = []

    if recipient_name and recipient_bank:
        recipient_display = recipient_name.title()
        lines.append(
            render_message(
                "transfer.format.funding_plan.recipient_title",
                locale,
                {
                    "amount": format_naira(amount),
                    "recipient_display": recipient_display,
                    "recipient_bank": recipient_bank,
                },
            )
        )
        if recipient_account:
            lines.append(
                render_message(
                    "transfer.format.funding_plan.account_line",
                    locale,
                    {"recipient_account": recipient_account},
                )
            )
        lines.append("")

    balance_str = format_naira(balance_available)
    lines.append(
        render_message(
            "transfer.format.funding_plan.primary_balance",
            locale,
            {"primary_bank": primary_bank, "balance": balance_str},
        )
    )
    lines.append("")

    amount_str = format_naira(secondary_amount)
    lines.append(
        render_message(
            "transfer.format.funding_plan.ask_use_secondary",
            locale,
            {"amount": amount_str, "secondary_bank": secondary_bank},
        )
    )
    lines.append("")

    lines.append(render_message("transfer.format.funding_plan.suggested_header", locale))
    for step in steps:
        bank = step.get(
            "bank_name",
            render_message("transfer.format.funding_plan.bank_fallback", locale),
        )
        amt = coerce_amount(step.get("amount"))
        lines.append(
            render_message(
                "transfer.format.funding_plan.suggested_item",
                locale,
                {"bank": bank, "amount": format_naira(amt)},
            )
        )

    lines.append(render_message("transfer.format.funding_plan.divider", locale))
    lines.append(
        render_message(
            "transfer.format.funding_plan.total_line",
            locale,
            {"amount": format_naira(amount)},
        )
    )

    return "\n".join(lines)
