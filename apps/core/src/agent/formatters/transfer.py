"""Transfer summary formatting utilities."""

from typing import Dict, Optional


def _format_currency_naira(amount: float) -> str:
    try:
        value = float(amount)
    except Exception:
        return f"₦{amount}"
    return f"₦{value:,.0f}"


def _calculate_transfer_fee(amount: float) -> float:
    try:
        amt = float(amount)
    except Exception:
        return 0.0
    fee = round(amt * 0.005)
    return float(max(fee, 10))


def format_transfer_summary(data: Dict) -> str:
    """Format a WhatsApp-friendly transfer confirmation summary.

    Expected keys in data:
      amount: float
      recipientName: str
      recipientBank: str
      recipientAccount: str
      sourceBank: str
      sourceAccount: str
      narration: Optional[str]
    """
    amount = float(data.get("amount", 0))
    recipient_name = str(data.get("recipientName") or "")
    recipient_bank = str(data.get("recipientBank") or "")
    recipient_account = str(data.get("recipientAccount") or "")
    source_bank = str(data.get("sourceBank") or "")
    source_account = str(data.get("sourceAccount") or "")
    narration: Optional[str] = data.get("narration")

    last4 = source_account[-4:] if source_account else "????"
    fee = _calculate_transfer_fee(amount)
    total = amount + fee

    lines = [
        f"Amount: *{_format_currency_naira(amount)}*",
        f"To: *{recipient_name.title()}* ({recipient_bank.title()} - ```{recipient_account}```)",
        f"From: {source_bank} (...{last4})",
    ]

    if narration:
        lines.append(f"Narration: _{narration}_")

    lines.append("")
    lines.append(f"*Fee:* {_format_currency_naira(fee)}")
    lines.append(f"*Total:* {_format_currency_naira(total)}")
    lines.append("")
    lines.append(
        "Tap the authorize button bellow, to enter your transaction pin.\n"
    )

    return "\n".join(lines)
