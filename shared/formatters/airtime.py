"""Airtime summary formatting utilities."""


def _format_currency_naira(amount: float) -> str:
    """Format amount as Nigerian Naira currency."""
    try:
        value = float(amount)
    except Exception:
        return f"₦{amount}"
    return f"₦{value:,.0f}"


def format_airtime_summary(data: dict) -> str:
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
    source_bank = str(data.get("sourceBank") or "Account")
    source_account = str(data.get("sourceAccount") or "")

    # Build recipient lines
    if recipient_name and recipient_name != recipient_phone:
        recipient_display = f"{recipient_name.title()} ({recipient_phone})"
    else:
        recipient_display = recipient_phone

    lines = [
        f"*{_format_currency_naira(amount)} Airtime → {recipient_display}*",
        f"Network: {network}",
    ]

    lines.append("")
    lines.append(f"From: {source_bank} (···{source_account[-4:] if source_account else '????'})")

    return "\n".join(lines)
