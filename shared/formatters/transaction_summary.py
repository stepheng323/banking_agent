"""Transaction summary formatting utilities for batch and multi-action transactions."""

from typing import Any

from shared.i18n import render_message


def format_amount(amount: float | int) -> str:
    """Format currency in Naira."""
    return f"₦{amount:,.2f}" if amount else "₦0.00"


def mask_account_number(account: str) -> str:
    """Mask account number to show last 4 digits only."""
    if not account:
        return ""
    return f"•••{account[-4:]}" if len(account) >= 4 else account


def format_batch_transfer_summary(
    num_transfers: int, total_amount: float, source_account_info: str | None, summaries: list[str], locale: str = "en"
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
    title = render_message("transaction_summary.batch.confirm_title", locale, {"count": num_transfers})
    total_str = render_message(
        "transaction_summary.batch.total",
        locale,
        {"amount": format_amount(total_amount).replace(".00", "")},
    )

    parts = [title]
    if source_account_info:
        parts.append(source_account_info)
    parts.append(total_str)
    parts.append("")
    parts.append("\n\n".join(summaries))

    return "\n".join(parts)


def format_multi_action_summary(completed_tasks: list, locale: str = "en") -> str:
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
    lines = [render_message("transaction_summary.multi.header", locale), ""]
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
                lines.append(
                    render_message(
                        "transaction_summary.multi.transfer_batch_line",
                        locale,
                        {"amount": format_amount(total_amount), "count": len(recipients)},
                    )
                )

                for r in recipients:
                    name = (
                        r.get("name")
                        or r.get("recipient_name")
                        or render_message("transaction_summary.multi.recipient_unknown", locale)
                    )
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
                recipient = task.payload.get("recipient_name") or render_message(
                    "transaction_summary.multi.recipient_fallback", locale
                )
                total_spent += amount
                lines.append(
                    render_message(
                        "transaction_summary.multi.transfer_single_line",
                        locale,
                        {"amount": format_amount(amount), "recipient": recipient},
                    )
                )

        lines.append("")

    # Handle airtime purchases
    if airtime_tasks:
        for task in airtime_tasks:
            amount = task.payload.get("amount", 0)
            phone = task.payload.get("phone_number") or render_message(
                "transaction_summary.multi.phone_fallback",
                locale,
            )
            network = task.payload.get("network", "")
            total_spent += amount
            lines.append(
                render_message(
                    "transaction_summary.multi.airtime_line",
                    locale,
                    {"amount": format_amount(amount), "phone": phone, "network": network},
                )
            )
        lines.append("")

    # Handle data purchases
    if data_tasks:
        for task in data_tasks:
            amount = task.payload.get("amount", 0)
            phone = task.payload.get("phone_number") or render_message(
                "transaction_summary.multi.phone_fallback",
                locale,
            )
            plan = task.payload.get("plan_name") or render_message(
                "transaction_summary.multi.data_plan_fallback",
                locale,
            )
            total_spent += amount
            lines.append(
                render_message(
                    "transaction_summary.multi.data_line",
                    locale,
                    {"plan": plan, "amount": format_amount(amount), "phone": phone},
                )
            )
        lines.append("")

    # Handle other task types
    if other_tasks:
        for task in other_tasks:
            lines.append(
                render_message(
                    "transaction_summary.multi.other_completed_line",
                    locale,
                    {"task_type": task.type.replace("_", " ").title()},
                )
            )
        lines.append("")

    # Add total if multiple transactions
    if len(completed_tasks) > 1:
        lines.append(
            render_message(
                "transaction_summary.multi.total_spent",
                locale,
                {"amount": format_amount(total_spent)},
            )
        )
        lines.append("")

    lines.append(render_message("transaction_summary.multi.success_footer", locale))

    return "\n".join(lines)


def format_intent_line(task_type: str, payload: dict[str, Any], locale: str = "en") -> str:
    """Generate a precise intent string for a task."""
    if task_type == "transfer":
        amount = payload.get("amount", 0)
        recipient = (
            payload.get("recipient_resolved_name")
            or payload.get("recipient_name")
            or render_message(
                "transaction_summary.intent.transfer_recipient_fallback",
                locale,
            )
        )
        return render_message(
            "transaction_summary.intent.transfer",
            locale,
            {"amount": format_amount(amount).replace(".00", ""), "recipient": recipient},
        )
    elif task_type == "airtime":
        amount = payload.get("amount", 0)
        phone = payload.get("recipient_phone") or render_message("transaction_summary.intent.phone_fallback", locale)
        return render_message(
            "transaction_summary.intent.airtime",
            locale,
            {"amount": format_amount(amount).replace(".00", ""), "phone": phone},
        )
    elif task_type == "data":
        plan = payload.get("plan_name") or render_message("transaction_summary.intent.data_plan_fallback", locale)
        phone = payload.get("target_phone") or render_message("transaction_summary.intent.phone_fallback", locale)
        return render_message("transaction_summary.intent.data", locale, {"plan": plan, "phone": phone})
    return render_message("transaction_summary.intent.generic", locale, {"task_type": task_type.title()})
