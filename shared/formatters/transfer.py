"""Transfer summary formatting utilities."""

from shared.i18n import render_message


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


def format_transfer_summary(data: dict, include_source: bool = True, locale: str = "en") -> str:
    """Format a WhatsApp-friendly transfer confirmation summary.

    Expected keys in data:
      amount: float
      recipientName: str
      recipientBank: str
      recipientAccount: str
      sourceBank: str
      sourceAccount: str
      narration: Optional[str]
      description: Optional[str]
      user_note: Optional[str]
    """
    amount = float(data.get("amount", 0))
    recipient_name = str(
        data.get("recipientName")
        or render_message("transfer.format.summary.recipient_fallback", locale)
    )
    recipient_bank = str(data.get("recipientBank") or "")
    recipient_account = str(data.get("recipientAccount") or "")
    source_bank = str(data.get("sourceBank") or "")
    source_account = str(data.get("sourceAccount") or "")
    user_note: str | None = data.get("user_note")

    last4 = (
        source_account[-4:]
        if source_account
        else render_message("transfer.format.summary.last4_fallback", locale)
    )

    lines = [
        render_message(
            "transfer.format.summary.title",
            locale,
            {"amount": _format_currency_naira(amount), "recipient_name": recipient_name.title()},
        ),
        render_message(
            "transfer.format.summary.recipient_line",
            locale,
            {"recipient_bank": recipient_bank.title(), "recipient_account": recipient_account},
        ),
    ]

    if user_note:
        lines.append(
            render_message(
                "transfer.format.summary.user_note",
                locale,
                {"user_note": user_note.strip().capitalize()},
            )
        )

    if include_source:
        lines.append("")
        lines.append(
            render_message(
                "transfer.format.summary.source_line",
                locale,
                {"source_bank": source_bank, "last4": last4},
            )
        )

    return "\n".join(lines)


def format_multi_source_transfer_summary(data: dict, locale: str = "en") -> str:
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
    recipient_name = str(
        data.get("recipientName")
        or render_message("transfer.format.summary.recipient_fallback", locale)
    )
    recipient_bank = str(data.get("recipientBank") or "")
    recipient_account = str(data.get("recipientAccount") or "")
    funding_sources: list[dict] = data.get("funding_sources", [])
    narration: str | None = data.get("narration")

    fee = _calculate_transfer_fee(amount)
    total = amount + fee

    lines = [
        render_message(
            "transfer.format.multi_source_summary.field_amount",
            locale,
            {"amount": _format_currency_naira(amount)},
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
        bank = source.get(
            "bank_name",
            render_message("transfer.format.multi_source_summary.bank_fallback", locale),
        )
        account = source.get("account_number", "")
        source_amount = float(source.get("amount", 0))
        last4 = (
            account[-4:]
            if account
            else render_message("transfer.format.summary.last4_fallback", locale)
        )
        lines.append(
            render_message(
                "transfer.format.multi_source_summary.funding_item",
                locale,
                {"bank": bank, "last4": last4, "amount": _format_currency_naira(source_amount)},
            )
        )

    lines.append("")
    lines.append(
        render_message(
            "transfer.format.multi_source_summary.fee",
            locale,
            {"fee": _format_currency_naira(fee)},
        )
    )
    lines.append(
        render_message(
            "transfer.format.multi_source_summary.total",
            locale,
            {"total": _format_currency_naira(total)},
        )
    )
    lines.append("")
    lines.append(render_message("transfer.format.multi_source_summary.authorize", locale))

    return "\n".join(lines)


def format_multi_source_receipt(data: dict, locale: str = "en") -> str:
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
    recipient_name = str(
        data.get("recipientName")
        or render_message("transfer.format.summary.recipient_fallback", locale)
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
            {"amount": _format_currency_naira(amount), "recipient_name": recipient_name.title()},
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
            bank = source.get(
                "bank_name",
                render_message("transfer.format.multi_source_summary.bank_fallback", locale),
            )
            account = source.get("account_number", "")
            source_amount = float(source.get("amount", 0))
            last4 = (
                account[-4:]
                if account
                else render_message("transfer.format.summary.last4_fallback", locale)
            )
            lines.append(
                render_message(
                    "transfer.format.multi_source_summary.funding_item",
                    locale,
                    {"bank": bank, "last4": last4, "amount": _format_currency_naira(source_amount)},
                )
            )
        lines.append("")
    else:
        source = funding_sources[0] if funding_sources else {}
        bank = source.get(
            "bank_name",
            render_message("transfer.format.multi_source_summary.bank_fallback", locale),
        )
        account = source.get("account_number", "")
        last4 = (
            account[-4:]
            if account
            else render_message("transfer.format.summary.last4_fallback", locale)
        )
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


def format_funding_plan_summary(
    steps: list[dict],
    amount: float,
    primary_bank: str,
    balance_available: float,
    recipient_name: str = "",
    recipient_bank: str = "",
    recipient_account: str = "",
    locale: str = "en",
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
            secondary_bank = step.get(
                "bank_name",
                render_message("transfer.format.funding_plan.secondary_bank_fallback", locale),
            )
            secondary_amount = float(step.get("amount", 0))
            break

    lines = []

    if recipient_name and recipient_bank:
        recipient_display = recipient_name.title()
        lines.append(
            render_message(
                "transfer.format.funding_plan.recipient_title",
                locale,
                {
                    "amount": _format_currency_naira(amount),
                    "recipient_display": recipient_display,
                    "recipient_bank": recipient_bank,
                },
            )
        )
        if recipient_account:
            lines.append(
                render_message(
                    "transfer.format.funding_plan.account_line",
                    locale,
                    {"recipient_account": recipient_account},
                )
            )
        lines.append("")

    balance_str = _format_currency_naira(balance_available)
    lines.append(
        render_message(
            "transfer.format.funding_plan.primary_balance",
            locale,
            {"primary_bank": primary_bank, "balance": balance_str},
        )
    )
    lines.append("")

    amount_str = _format_currency_naira(secondary_amount)
    lines.append(
        render_message(
            "transfer.format.funding_plan.ask_use_secondary",
            locale,
            {"amount": amount_str, "secondary_bank": secondary_bank},
        )
    )
    lines.append("")

    lines.append(render_message("transfer.format.funding_plan.suggested_header", locale))
    for step in steps:
        bank = step.get(
            "bank_name",
            render_message("transfer.format.funding_plan.bank_fallback", locale),
        )
        amt = float(step.get("amount", 0))
        lines.append(
            render_message(
                "transfer.format.funding_plan.suggested_item",
                locale,
                {"bank": bank, "amount": _format_currency_naira(amt)},
            )
        )

    lines.append(render_message("transfer.format.funding_plan.divider", locale))
    lines.append(
        render_message(
            "transfer.format.funding_plan.total_line",
            locale,
            {"amount": _format_currency_naira(amount)},
        )
    )

    return "\n".join(lines)


def format_transfer_success_message(
    amount: float,
    recipient_name: str,
    transaction_id: str,
    locale: str = "en",
) -> str:
    """Format transfer success notification message.

    Args:
        amount: Transfer amount
        recipient_name: Name of recipient
        transaction_id: Provider transaction ID

    Returns:
        WhatsApp-formatted success message
    """
    return render_message(
        "transfer.format.notifications.success",
        locale,
        {
            "amount": _format_currency_naira(amount),
            "recipient_name": recipient_name,
            "transaction_id": transaction_id,
        },
    )


def format_transfer_pending_message(
    amount: float,
    recipient_name: str,
    locale: str = "en",
) -> str:
    """Format transfer pending notification message.

    Args:
        amount: Transfer amount
        recipient_name: Name of recipient

    Returns:
        WhatsApp-formatted pending message
    """
    return render_message(
        "transfer.format.notifications.pending",
        locale,
        {
            "amount": _format_currency_naira(amount),
            "recipient_name": recipient_name,
        },
    )


def format_transfer_queued_message(
    amount: float,
    recipient_name: str,
    locale: str = "en",
) -> str:
    """
    Sent immediately after PIN verification when transfer is queued for processing.

    Args:
        amount: Transfer amount
        recipient_name: Name of recipient

    Returns:
        WhatsApp-formatted acknowledgment message
    """
    return render_message(
        "transfer.format.notifications.queued",
        locale,
        {"amount": _format_currency_naira(amount), "recipient_name": recipient_name},
    )
