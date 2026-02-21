"""Airtime summary formatting utilities."""

from shared.i18n import render_message


def _format_currency_naira(amount: float) -> str:
    """Format amount as Nigerian Naira currency."""
    try:
        value = float(amount)
    except Exception:
        return f"₦{amount}"
    return f"₦{value:,.0f}"


def format_airtime_summary(data: dict, locale: str = "en") -> str:
    """
    Format a WhatsApp-friendly airtime purchase confirmation summary.

    Expected keys in data:
      amount: float
      recipientPhone: str
      network: str
      recipientName: Optional[str]
      sourceBank: str
      sourceAccount: str
    """
    amount = float(data.get("amount", 0))
    recipient_phone = str(data.get("recipientPhone") or "")
    network = str(data.get("network") or "")
    recipient_name = data.get("recipientName")
    source_bank = str(data.get("sourceBank") or render_message("airtime.format.summary.source_bank_fallback", locale))
    source_account = str(data.get("sourceAccount") or "")

    # Build recipient lines
    if recipient_name and recipient_name != recipient_phone:
        recipient_display = render_message(
            "airtime.format.summary.recipient_with_phone",
            locale,
            {"recipient_name": recipient_name.title(), "recipient_phone": recipient_phone},
        )
    else:
        recipient_display = recipient_phone

    lines = [
        render_message(
            "airtime.format.summary.title",
            locale,
            {"amount": _format_currency_naira(amount), "recipient_display": recipient_display},
        ),
        render_message("airtime.format.summary.network_line", locale, {"network": network}),
    ]

    lines.append("")
    lines.append(
        render_message(
            "airtime.format.summary.source_line",
            locale,
            {
                "source_bank": source_bank,
                "last4": source_account[-4:] if source_account else render_message("airtime.format.summary.last4_fallback", locale),
            },
        )
    )

    return "\n".join(lines)
