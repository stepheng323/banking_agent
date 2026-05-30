"""Funding-related message formatting utilities."""

from typing import Any

from banking.presentation.formatters.currency import coerce_amount, format_naira
from banking.presentation.i18n.renderer import render_message
from shared.money import MoneyAmount


def format_insufficient_funds(
    transfer_amount: MoneyAmount,
    bank_name: str,
    available_balance: MoneyAmount,
    max_available: MoneyAmount | None = None,
    recipient_name: str = "",
    locale: str = "en",
) -> str:
    """Format insufficient funds message.

    Args:
        transfer_amount: Amount user is trying to send
        bank_name: Name of the primary bank account
        available_balance: Current available balance in primary account
        max_available: Maximum available across all accounts (defaults to available_balance)
        recipient_name: Name of the recipient

    Returns:
        WhatsApp-formatted error message
    """
    lines = []

    lines.append(render_message("funding.format.insufficient.header", locale))
    lines.append("")

    recipient_display = (
        recipient_name.title()
        if recipient_name
        else render_message(
            "funding.format.insufficient.recipient_fallback",
            locale,
        )
    )
    transfer_amount_str = format_naira(transfer_amount)
    balance_str = format_naira(available_balance)

    lines.append(
        render_message(
            "funding.format.insufficient.body",
            locale,
            {
                "transfer_amount": transfer_amount_str,
                "recipient_display": recipient_display,
                "bank_name": bank_name,
                "balance": balance_str,
            },
        )
    )
    lines.append("")

    lines.append(render_message("funding.format.insufficient.options_header", locale))
    lines.append(
        render_message(
            "funding.format.insufficient.option_send_instead",
            locale,
            {"amount": format_naira(max_available if max_available is not None else available_balance)},
        )
    )
    lines.append(render_message("funding.format.insufficient.option_add_funds_retry", locale))
    lines.append(render_message("funding.format.insufficient.option_cancel", locale))

    return "\n".join(lines)


def format_funding_plan_message(
    transfer_amount: MoneyAmount,
    steps: list[dict[str, Any]],
    locale: str = "en",
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
        amount_str = format_naira(transfer_amount)
        bank = step.get("bank_name", render_message("funding.format.plan.bank_fallback_lower", locale))
        return render_message(
            "funding.format.plan.single_source_debit",
            locale,
            {"amount": amount_str, "bank": bank},
        )

    lines = [
        render_message(
            "funding.format.plan.multi_source_header",
            locale,
            {"amount": format_naira(transfer_amount)},
        )
    ]
    for step in steps:
        bank = step.get("bank_name", render_message("funding.format.plan.bank_fallback", locale))
        amount = coerce_amount(step.get("amount"))
        lines.append(
            render_message(
                "funding.format.plan.multi_source_item",
                locale,
                {"amount": format_naira(amount), "bank": bank},
            )
        )

    return "\n".join(lines)
