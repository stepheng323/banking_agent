"""Single-transfer confirmation summary formatting."""

from __future__ import annotations

from banking.presentation.formatters.accounts import format_source_account_info_from_account_number
from banking.presentation.formatters.currency import coerce_amount, format_naira
from banking.presentation.formatters.transfer_common import resolve_display_narration
from banking.presentation.i18n.personality import PersonalityContext, render_personalized_message
from banking.presentation.i18n.renderer import render_message
from shared.utils.bank_aliases import display_bank_name


def format_transfer_summary(
    data: dict,
    include_source: bool = True,
    locale: str = "en",
    personality_context: PersonalityContext | None = None,
) -> str:
    """Format a WhatsApp-friendly transfer confirmation summary."""
    amount = coerce_amount(data.get("amount"))
    recipient_name = str(
        data.get("recipientName") or render_message("transfer.format.summary.recipient_fallback", locale)
    )
    recipient_bank = str(display_bank_name(data.get("recipientBank")) or "")
    recipient_account = str(data.get("recipientAccount") or "")
    source_bank = str(display_bank_name(data.get("sourceBank")) or "")
    source_account = str(data.get("sourceAccount") or "")
    display_narration = resolve_display_narration(data)
    lines = [
        render_personalized_message(
            "transfer.format.summary.title",
            locale,
            {"amount": format_naira(amount), "recipient_name": recipient_name.title()},
            personality_context,
        ),
        render_message(
            "transfer.format.summary.recipient_line",
            locale,
            {"recipient_bank": recipient_bank, "recipient_account": recipient_account},
        ),
    ]

    if display_narration:
        lines.append(
            render_message(
                "transfer.format.summary.user_note",
                locale,
                {"user_note": display_narration.strip().capitalize()},
            )
        )

    if include_source:
        lines.append("")
        lines.append(
            format_source_account_info_from_account_number(
                bank=source_bank,
                account_number=source_account,
                locale=locale,
            )
        )

    return "\n".join(lines)
