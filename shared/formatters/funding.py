"""Funding-related message formatting utilities."""

from typing import Any

from shared.i18n import render_message


def _format_currency_naira(amount: float) -> str:
    """Format amount as Naira currency."""
    try:
        value = float(amount)
    except Exception:
        return f"₦{amount}"
    return f"₦{value:,.0f}"


def format_insufficient_funds(
    transfer_amount: float,
    bank_name: str,
    available_balance: float,
    max_available: float = 0,
    recipient_name: str = "",
    recipient_bank: str = "",
    recipient_account: str = "",
    locale: str = "en",
) -> str:
    """Format insufficient funds message.

    Args:
        transfer_amount: Amount user is trying to send
        bank_name: Name of the primary bank account
        available_balance: Current available balance in primary account
        max_available: Maximum available across all accounts (defaults to available_balance)
        recipient_name: Name of the recipient
        recipient_bank: Recipient's bank name
        recipient_account: Recipient's account number

    Returns:
        WhatsApp-formatted error message
    """

    lines = []

    # We'll drop the transaction summary header (lines 45-53) and integrate it into the text as per Option B design.
    # Logic:

    lines.append(render_message("funding.format.insufficient.header", locale))
    lines.append("")

    recipient_display = recipient_name.title() if recipient_name else render_message(
        "funding.format.insufficient.recipient_fallback",
        locale,
    )
    transfer_amount_str = _format_currency_naira(transfer_amount)
    balance_str = _format_currency_naira(available_balance)

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
            {"amount": _format_currency_naira(max_available)},
        )
    )
    lines.append(render_message("funding.format.insufficient.option_add_funds_retry", locale))
    lines.append(render_message("funding.format.insufficient.option_cancel", locale))

    return "\n".join(lines)


def format_funding_plan_message(
    transfer_amount: float,
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
        amount_str = _format_currency_naira(transfer_amount)
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
            {"amount": _format_currency_naira(transfer_amount)},
        )
    ]
    for step in steps:
        bank = step.get("bank_name", render_message("funding.format.plan.bank_fallback", locale))
        amount = float(step.get("amount", 0))
        lines.append(
            render_message(
                "funding.format.plan.multi_source_item",
                locale,
                {"amount": _format_currency_naira(amount), "bank": bank},
            )
        )

    return "\n".join(lines)
