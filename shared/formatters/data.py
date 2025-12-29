"""Data purchase formatting utilities."""


def format_data_success_message(
    plan_name: str,
    amount: int,
    target_phone: str,
    source: str,
) -> str:
    """
    Format data purchase success message.

    Args:
        plan_name: Name of the data plan purchased
        amount: Amount in Naira
        target_phone: Phone number that received data
        source: 'self' or 'other'

    Returns:
        Formatted success message
    """
    masked_phone = target_phone[:4] + "•••" + target_phone[-4:]

    if source == "other":
        return f"✅ *Data Purchase Successful!*\n\n*{plan_name}* for ₦{amount:,}\nSent to: {masked_phone}"
    else:
        return f"✅ *Data Purchase Successful!*\n\nYour *{plan_name}* is now active.\nAmount: ₦{amount:,}"


def format_data_failure_message(error: str) -> str:
    """
    Format data purchase failure message.

    Args:
        error: Error message from provider

    Returns:
        User-friendly failure message
    """
    error_lower = error.lower()

    if "insufficient" in error_lower or "balance" in error_lower:
        return "Purchase failed: Insufficient balance.\n\nWould you like to try a smaller plan?"
    elif "network" in error_lower:
        return "Purchase failed: Network provider error.\n\nPlease try again in a moment."
    else:
        return f"Purchase failed: {error}\n\nPlease try again or contact support."


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
