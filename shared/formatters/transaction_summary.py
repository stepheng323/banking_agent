from typing import Any

"""Transaction summary formatting utilities for batch and multi-action transactions."""


def format_amount(amount: float | int) -> str:
    """Format currency in Naira."""
    return f"₦{amount:,.2f}" if amount else "₦0.00"


def mask_account_number(account: str) -> str:
    """Mask account number to show last 4 digits only."""
    if not account:
        return ""
    return f"•••{account[-4:]}" if len(account) >= 4 else account


def format_batch_transfer_summary(
    num_transfers: int, total_amount: float, source_account_info: str | None, summaries: list[str]
) -> str:
    """Format a confirmation summary for a batch of transfers.

    Args:
        num_transfers: Number of transfers in the batch
        total_amount: Total amount of all transfers
        source_account_info: Formatted source account string (e.g. "From: GT Bank (···1234)")
        summaries: List of individual transfer summaries

    Returns:
        WhatsApp-formatted batch transfer confirmation
    """
    title = f"*Confirm Transfers ({num_transfers})*"
    total_str = f"Total: {format_amount(total_amount)}".replace(".00", "")

    parts = [title]
    if source_account_info:
        parts.append(source_account_info)
    parts.append(total_str)
    parts.append("")
    parts.append("\n\n".join(summaries))

    return "\n".join(parts)


def format_multi_action_summary(completed_tasks: list) -> str:
    """Format a text summary for batch/multi-action transactions.

    Args:
        completed_tasks: List of completed TaskSpec objects

    Returns:
        WhatsApp-formatted transaction summary

    Example:
        ✅ *Transaction Summary*

        *Transfer:* ₦100,000.00 to 2 recipients
          ✓ Mum - ₦50,000.00 (GT Bank •••1234)
          ✓ Tolu - ₦50,000.00 (Access Bank •••5678)

        ✓ *Airtime:* ₦1,000.00 for 08012345678 (MTN)

        *Total Spent:* ₦101,000.00

        _All transactions completed successfully_
    """
    lines = ["✅ *Transaction Summary*", ""]
    total_spent = 0

    # Group by task type
    transfer_tasks = [t for t in completed_tasks if t.type == "transfer"]
    airtime_tasks = [t for t in completed_tasks if t.type == "airtime"]
    data_tasks = [t for t in completed_tasks if t.type == "data"]
    other_tasks = [t for t in completed_tasks if t.type not in ["transfer", "airtime", "data"]]

    # Handle batch/multiple transfers
    if transfer_tasks:
        for task in transfer_tasks:
            recipients = task.payload.get("recipients", [])
            is_batch = task.payload.get("is_batch", False) or len(recipients) > 1

            if is_batch and recipients:
                # Batch transfer
                total_amount = sum(r.get("amount", 0) for r in recipients)
                total_spent += total_amount
                lines.append(f"*Transfer:* {format_amount(total_amount)} to {len(recipients)} recipients")

                for r in recipients:
                    name = r.get("name") or r.get("recipient_name", "Unknown")
                    amount = r.get("amount", 0)
                    bank = r.get("bank_name") or r.get("recipient_bank_name", "")
                    account = r.get("account") or r.get("recipient_account", "")
                    status = r.get("status", "success")

                    masked_account = mask_account_number(account)
                    status_icon = "✓" if status == "success" else "✗"
                    lines.append(f"  {status_icon} {name} - {format_amount(amount)} ({bank} {masked_account})")
            else:
                # Single transfer in multi-action context
                amount = task.payload.get("amount", 0)
                recipient = task.payload.get("recipient_name", "recipient")
                total_spent += amount
                lines.append(f"✓ *Transfer:* {format_amount(amount)} to {recipient}")

        lines.append("")

    # Handle airtime purchases
    if airtime_tasks:
        for task in airtime_tasks:
            amount = task.payload.get("amount", 0)
            phone = task.payload.get("phone_number", "N/A")
            network = task.payload.get("network", "")
            total_spent += amount
            lines.append(f"✓ *Airtime:* {format_amount(amount)} for {phone} ({network})")
        lines.append("")

    # Handle data purchases
    if data_tasks:
        for task in data_tasks:
            amount = task.payload.get("amount", 0)
            phone = task.payload.get("phone_number", "N/A")
            plan = task.payload.get("plan_name", "data")
            total_spent += amount
            lines.append(f"✓ *Data:* {plan} - {format_amount(amount)} for {phone}")
        lines.append("")

    # Handle other task types
    if other_tasks:
        for task in other_tasks:
            lines.append(f"✓ *{task.type.replace('_', ' ').title()}:* Completed")
        lines.append("")

    # Add total if multiple transactions
    if len(completed_tasks) > 1:
        lines.append(f"*Total Spent:* {format_amount(total_spent)}")
        lines.append("")

    lines.append("_All transactions completed successfully_")

    return "\n".join(lines)


def format_intent_line(task_type: str, payload: dict[str, Any]) -> str:
    """Generate a precise intent string for a task."""
    if task_type == "transfer":
        amount = payload.get("amount", 0)
        recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name") or "Recipient"
        return f"{format_amount(amount).replace('.00', '')} → {recipient}"
    elif task_type == "airtime":
        amount = payload.get("amount", 0)
        phone = payload.get("recipient_phone") or "your line"
        return f"{format_amount(amount).replace('.00', '')} airtime recharge on {phone}"
    elif task_type == "data":
        plan = payload.get("plan_name") or "Data"
        phone = payload.get("target_phone") or "your line"
        return f"Buy {plan} for {phone}"
    return f"{task_type.title()} transaction"
