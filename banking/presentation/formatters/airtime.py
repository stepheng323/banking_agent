"""Airtime summary formatting utilities."""

from banking.presentation.formatters.accounts import format_source_account_info_from_account_number
from banking.presentation.formatters.currency import coerce_amount, format_naira
from banking.presentation.i18n.personality import PersonalityContext, render_personalized_message
from banking.presentation.i18n.renderer import render_message
from shared.utils.network_utils import format_network_display_name


def format_airtime_summary(
    data: dict,
    locale: str = "en",
    personality_context: PersonalityContext | None = None,
) -> str:
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
    recipient_name = str(data.get("recipientName") or "").strip()
    network = format_network_display_name(data.get("network"))
    source_bank = str(data.get("sourceBank") or render_message("airtime.format.summary.source_bank_fallback", locale))
    source_account = str(data.get("sourceAccount") or "")
    is_self = data.get("isSelf", False)

    if is_self:
        self_label = render_message("airtime.format.summary.target_self", locale)
        recipient_display = f"{self_label} ({recipient_phone})" if recipient_phone else self_label
    elif recipient_name:
        recipient_display = f"{recipient_name} ({recipient_phone})" if recipient_phone else recipient_name
    else:
        recipient_display = recipient_phone

    lines = [
        render_personalized_message(
            "airtime.format.summary.title",
            locale,
            {"amount": format_naira(amount), "recipient_display": recipient_display},
            personality_context,
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
