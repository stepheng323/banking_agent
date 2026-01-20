"""Data purchase formatting utilities."""


def format_data_plan_suggestion(
    plan_name: str,
    network: str,
    amount: int,
    validity_days: int | None,
    size_gb: float | None,
    reason: str,
) -> str:
    """
    Format data plan suggestion message.

    Args:
        plan_name: Full plan name
        network: Network provider
        amount: Price in Naira
        validity_days: Validity in days
        size_gb: Data size in GB
        reason: 'repeat', 'budget', or 'default'

    Returns:
        Formatted suggestion message
    """
    validity = f"{validity_days} days" if validity_days else ""
    size = f"{size_gb}GB" if size_gb else plan_name

    if reason == "repeat":
        return (
            f"You usually buy *{size} {network}* ({validity}) for ₦{amount:,}.\n"
            f"Want me to get that again?\n\n"
            f"Reply *yes* to continue or *show plans* to see other options."
        )
    elif reason == "budget":
        return (
            f"With ₦{amount:,}, the best option is:\n"
            f"• *{network} {size}* — valid {validity}\n\n"
            f"Reply *yes* to continue or *show plans* to choose."
        )
    else:
        return (
            f"I recommend:\n"
            f"• *{network} {size}* — ₦{amount:,} ({validity})\n\n"
            f"Reply *yes* to continue or *show plans* to see other options."
        )


def format_data_plan_list(
    plans: list[dict],
    network: str,
    has_more: bool = False,
) -> str:
    """
    Format paginated data plan list.

    Args:
        plans: List of plan dicts with size_gb, amount, validity_days
        network: Network provider
        has_more: Whether there are more plans to show

    Returns:
        Formatted plan list
    """
    lines = [f"*{network} Data Plans:*\n"]

    for plan in plans:
        size = f"{plan.get('size_gb')}GB" if plan.get("size_gb") else plan.get("name", "")
        validity = f"{plan.get('validity_days')} days" if plan.get("validity_days") else ""
        amount = plan.get("amount", 0)
        lines.append(f"• *{size}* — ₦{amount:,} ({validity})")

    lines.append("")
    if has_more:
        lines.append("Reply with plan size (e.g. *5GB*) or *show more*.")
    else:
        lines.append("Reply with plan size (e.g. *5GB*) to select.")

    return "\n".join(lines)


def format_data_summary(data: dict) -> str:
    """
    Format a WhatsApp-friendly data purchase confirmation summary.

    Expected keys in data:
      planName: str
      amount: float
      recipientPhone: str
      network: str
      sourceBank: str
      sourceAccount: str
      isSelf: bool
    """
    plan_name = data.get("planName", "Data Plan")
    amount = float(data.get("amount", 0))
    recipient_phone = str(data.get("recipientPhone") or "")
    network = str(data.get("network") or "")
    source_bank = str(data.get("sourceBank") or "Account")
    source_account = str(data.get("sourceAccount") or "")
    is_self = data.get("isSelf", False)

    target_display = "your number" if is_self else recipient_phone

    lines = [
        f"*{plan_name} → {target_display}*",
        f"Network: {network} • Amount: ₦{amount:,.0f}",
    ]

    lines.append("")
    lines.append(f"From: {source_bank} (···{source_account[-4:] if source_account else '????'})")

    return "\n".join(lines)
