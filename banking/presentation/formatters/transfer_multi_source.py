"""Multi-source transfer summary and receipt formatting."""

from __future__ import annotations

from banking.presentation.formatters.currency import coerce_amount, format_naira
from banking.presentation.formatters.transfer_common import calculate_transfer_fee, resolve_display_narration
from banking.presentation.i18n.renderer import render_message


def format_multi_source_transfer_summary(data: dict, locale: str = "en") -> str:
    """Format transfer confirmation for multi-account funding."""
    amount = coerce_amount(data.get("amount"))
    recipient_name = str(
        data.get("recipientName") or render_message("transfer.format.summary.recipient_fallback", locale)
    )
    recipient_bank = str(data.get("recipientBank") or "")
    recipient_account = str(data.get("recipientAccount") or "")
    funding_sources: list[dict] = data.get("funding_sources", [])
    narration = resolve_display_narration(data)

    fee = calculate_transfer_fee(amount)
    total = amount + fee

    lines = [
        render_message(
            "transfer.format.multi_source_summary.field_amount",
            locale,
            {"amount": format_naira(amount)},
        ),
        render_message(
            "transfer.format.multi_source_summary.field_to",
            locale,
            {"recipient_name": recipient_name.title()},
        ),
        render_message(
            "transfer.format.multi_source_summary.field_bank",
            locale,
            {"recipient_bank": recipient_bank.title()},
        ),
        render_message(
            "transfer.format.multi_source_summary.field_account",
            locale,
            {"recipient_account": recipient_account},
        ),
    ]

    if narration:
        lines.append(
            render_message(
                "transfer.format.multi_source_summary.narration",
                locale,
                {"narration": narration.strip().capitalize()},
            )
        )

    lines.append("")
    lines.append(render_message("transfer.format.multi_source_summary.funding_header", locale))

    for source in funding_sources:
        lines.append(_format_funding_source_item(source, locale=locale))

    lines.append("")
    lines.append(
        render_message(
            "transfer.format.multi_source_summary.fee",
            locale,
            {"fee": format_naira(fee)},
        )
    )
    lines.append(
        render_message(
            "transfer.format.multi_source_summary.total",
            locale,
            {"total": format_naira(total)},
        )
    )
    lines.append("")
    lines.append(render_message("transfer.format.multi_source_summary.authorize", locale))

    return "\n".join(lines)


def format_multi_source_receipt(data: dict, locale: str = "en") -> str:
    """Format receipt for completed multi-account transfer."""
    amount = coerce_amount(data.get("amount"))
    recipient_name = str(
        data.get("recipientName") or render_message("transfer.format.summary.recipient_fallback", locale)
    )
    recipient_bank = str(data.get("recipientBank") or "")
    recipient_account = str(data.get("recipientAccount") or "")
    funding_sources: list[dict] = data.get("funding_sources", [])
    reference = data.get("reference", "")
    timestamp = data.get("timestamp", "")

    lines = [
        render_message("transfer.format.multi_source_receipt.success_header", locale),
        "",
        render_message(
            "transfer.format.multi_source_receipt.title",
            locale,
            {"amount": format_naira(amount), "recipient_name": recipient_name.title()},
        ),
        render_message(
            "transfer.format.multi_source_receipt.location_line",
            locale,
            {"recipient_bank": recipient_bank.title(), "recipient_account": recipient_account},
        ),
        "",
    ]

    if len(funding_sources) > 1:
        lines.append(render_message("transfer.format.multi_source_receipt.funded_header", locale))
        for source in funding_sources:
            lines.append(_format_funding_source_item(source, locale=locale))
        lines.append("")
    else:
        source = funding_sources[0] if funding_sources else {}
        bank = source.get(
            "bank_name",
            render_message("transfer.format.multi_source_summary.bank_fallback", locale),
        )
        account = source.get("account_number", "")
        last4 = account[-4:] if account else render_message("transfer.format.summary.last4_fallback", locale)
        lines.append(
            render_message(
                "transfer.format.multi_source_receipt.from_line",
                locale,
                {"bank": bank, "last4": last4},
            )
        )
        lines.append("")

    if reference:
        lines.append(
            render_message(
                "transfer.format.multi_source_receipt.ref",
                locale,
                {"reference": reference},
            )
        )

    if timestamp:
        lines.append(
            render_message(
                "transfer.format.multi_source_receipt.time",
                locale,
                {"time": timestamp},
            )
        )

    return "\n".join(lines)


def _format_funding_source_item(source: dict, *, locale: str) -> str:
    bank = source.get(
        "bank_name",
        render_message("transfer.format.multi_source_summary.bank_fallback", locale),
    )
    account = source.get("account_number", "")
    source_amount = coerce_amount(source.get("amount"))
    last4 = account[-4:] if account else render_message("transfer.format.summary.last4_fallback", locale)
    return render_message(
        "transfer.format.multi_source_summary.funding_item",
        locale,
        {"bank": bank, "last4": last4, "amount": format_naira(source_amount)},
    )
