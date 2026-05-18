"""Airtime summary formatting utilities."""

from shared.formatters.accounts import format_source_account_info_from_account_number
from shared.formatters.currency import coerce_amount, format_naira
from shared.i18n import render_message


def format_airtime_summary(data: dict, locale: str = "en") -> str:
    """
    Format a WhatsApp-friendly airtime purchase confirmation summary.

    Expected keys in data:
      amount: float
      recipientPhone: str
      network: str
      sourceBank: str
      sourceAccount: str
    """
    amount = coerce_amount(data.get("amount"))
    recipient_phone = str(data.get("recipientPhone") or "")
    network = str(data.get("network") or "")
    source_bank = str(data.get("sourceBank") or render_message("airtime.format.summary.source_bank_fallback", locale))
    source_account = str(data.get("sourceAccount") or "")

    lines = [
        render_message(
            "airtime.format.summary.title",
            locale,
            {"amount": format_naira(amount), "recipient_display": recipient_phone},
        ),
        render_message("airtime.format.summary.network_line", locale, {"network": network}),
    ]

    lines.append("")
    lines.append(
        format_source_account_info_from_account_number(
            bank=source_bank,
            account_number=source_account,
            locale=locale,
        )
    )

    return "\n".join(lines)
