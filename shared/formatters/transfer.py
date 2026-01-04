"""Transfer summary formatting utilities."""


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


def format_transfer_summary(data: dict) -> str:
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
    narration: str | None = data.get("narration")

    last4 = source_account[-4:] if source_account else "????"

    lines = [
        f"*{_format_currency_naira(amount)} → {recipient_name.title()}*",
        f"{recipient_bank.title()} • {recipient_account}",
    ]

    if narration:
        lines.append(f"Note: {narration}")

    lines.append("")
    lines.append(f"From: {source_bank} (···{last4})")

    return "\n".join(lines)


def format_multi_source_transfer_summary(data: dict) -> str:
    """Format transfer confirmation for multi-account funding.

    Expected keys in data:
      amount: float
      recipientName: str
      recipientBank: str
      recipientAccount: str
      funding_sources: List[Dict] - each with bank_name, account_number, amount
      narration: Optional[str]
    """
    amount = float(data.get("amount", 0))
    recipient_name = str(data.get("recipientName") or "")
    recipient_bank = str(data.get("recipientBank") or "")
    recipient_account = str(data.get("recipientAccount") or "")
    funding_sources: list[dict] = data.get("funding_sources", [])
    narration: str | None = data.get("narration")

    fee = _calculate_transfer_fee(amount)
    total = amount + fee

    lines = [
        f"*Amount:* {_format_currency_naira(amount)}",
        f"*To:* {recipient_name.title()}",
        f"*Bank:* {recipient_bank.title()}",
        f"*Account:* `{recipient_account}`",
    ]

    if narration:
        lines.append(f"*Note:* {narration}")

    lines.append("")
    lines.append("*Funding from:*")

    for source in funding_sources:
        bank = source.get("bank_name", "Account")
        account = source.get("account_number", "")
        source_amount = float(source.get("amount", 0))
        last4 = account[-4:] if account else "????"
        lines.append(f"  • {bank} (···{last4}): {_format_currency_naira(source_amount)}")

    lines.append("")
    lines.append(f"*Fee:* {_format_currency_naira(fee)}")
    lines.append(f"*Total:* {_format_currency_naira(total)}")
    lines.append("")
    lines.append("Tap *Authorize* to enter your PIN.")

    return "\n".join(lines)


def format_multi_source_receipt(data: dict) -> str:
    """Format receipt for completed multi-account transfer.

    Expected keys in data:
      amount: float
      recipientName: str
      recipientBank: str
      recipientAccount: str
      funding_sources: List[Dict] - each with bank_name, account_number, amount
      reference: str
      timestamp: Optional[str]
    """
    amount = float(data.get("amount", 0))
    recipient_name = str(data.get("recipientName") or "")
    recipient_bank = str(data.get("recipientBank") or "")
    recipient_account = str(data.get("recipientAccount") or "")
    funding_sources: list[dict] = data.get("funding_sources", [])
    reference = data.get("reference", "")
    timestamp = data.get("timestamp", "")

    lines = [
        "✓ *Transfer Successful!*",
        "",
        f"*{_format_currency_naira(amount)}* → {recipient_name.title()}",
        f"📍 {recipient_bank.title()} (`{recipient_account}`)",
        "",
    ]

    if len(funding_sources) > 1:
        lines.append("*Funded from:*")
        for source in funding_sources:
            bank = source.get("bank_name", "Account")
            account = source.get("account_number", "")
            source_amount = float(source.get("amount", 0))
            last4 = account[-4:] if account else "????"
            lines.append(f"  • {bank} (···{last4}): {_format_currency_naira(source_amount)}")
        lines.append("")
    else:
        source = funding_sources[0] if funding_sources else {}
        bank = source.get("bank_name", "Account")
        account = source.get("account_number", "")
        last4 = account[-4:] if account else "????"
        lines.append(f"*From:* {bank} (···{last4})")
        lines.append("")

    if reference:
        lines.append(f"*Ref:* `{reference}`")

    if timestamp:
        lines.append(f"*Time:* {timestamp}")

    return "\n".join(lines)


def format_funding_plan_summary(
    steps: list[dict],
    amount: float,
    primary_bank: str,
    balance_available: float,
    recipient_name: str = "",
    recipient_bank: str = "",
    recipient_account: str = "",
) -> str:
    """Format funding plan summary for multi-account transfer authorization.

    Args:
        steps: List of funding steps, each with bank_name and amount
        amount: Total transfer amount
        primary_bank: Primary bank name (where balance is insufficient)
        balance_available: Current balance in primary bank
        recipient_name: Name of the recipient
        recipient_bank: Recipient's bank name
        recipient_account: Recipient's account number

    Returns:
        WhatsApp-formatted funding plan summary
    """
    secondary_bank = None
    secondary_amount = 0.0
    for step in steps:
        if step.get("bank_name") != primary_bank:
            secondary_bank = step.get("bank_name", "another account")
            secondary_amount = float(step.get("amount", 0))
            break

    lines = []

    if recipient_name and recipient_bank:
        recipient_display = recipient_name.title()
        lines.append(f"*{_format_currency_naira(amount)} → {recipient_display} ({recipient_bank})*")
        if recipient_account:
            lines.append(f"Account: {recipient_account}")
        lines.append("")

    balance_str = _format_currency_naira(balance_available)
    lines.append(f"Your {primary_bank} has *{balance_str}* — not enough for this transfer.")
    lines.append("")

    amount_str = _format_currency_naira(secondary_amount)
    lines.append(f"Would you like to use *{amount_str}* from your {secondary_bank} to complete it?")
    lines.append("")

    lines.append("*Suggested breakdown:*")
    for step in steps:
        bank = step.get("bank_name", "Account")
        amt = float(step.get("amount", 0))
        lines.append(f"• {bank}: *{_format_currency_naira(amt)}*")

    lines.append("─────────────")
    lines.append(f"*Total:* {_format_currency_naira(amount)}")

    return "\n".join(lines)


def format_transfer_success_message(
    amount: float,
    recipient_name: str,
    transaction_id: str,
) -> str:
    """Format transfer success notification message.

    Args:
        amount: Transfer amount
        recipient_name: Name of recipient
        transaction_id: Provider transaction ID

    Returns:
        WhatsApp-formatted success message
    """
    return (
        f"✓ Transfer successful! {_format_currency_naira(amount)} has been sent to "
        f"{recipient_name}. Transaction ID: {transaction_id}"
    )


def format_transfer_pending_message(
    amount: float,
    recipient_name: str,
) -> str:
    """Format transfer pending notification message.

    Args:
        amount: Transfer amount
        recipient_name: Name of recipient

    Returns:
        WhatsApp-formatted pending message
    """
    return (
        f"⏳ Your {_format_currency_naira(amount)} transfer to {recipient_name} is processing.\n\n"
        "You'll receive confirmation shortly. If you don't receive it within 5 minutes,\n"
        "please contact support."
    )


def format_transfer_queued_message(
    amount: float,
    recipient_name: str,
) -> str:
    """
    Sent immediately after PIN verification when transfer is queued for processing.

    Args:
        amount: Transfer amount
        recipient_name: Name of recipient

    Returns:
        WhatsApp-formatted acknowledgment message
    """
    return (
        f"✓ Your transfer of {_format_currency_naira(amount)} to {recipient_name} "
        "has been authorized and is being processed."
    )
