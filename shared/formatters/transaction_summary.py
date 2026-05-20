"""Transaction summary formatting utilities for batch and multi-action transactions."""

from typing import Any

from shared.formatters.currency import format_naira
from shared.formatters.recipient_display import format_summary_recipient_display_label
from shared.formatters.transaction_copy import build_completion_frame, derive_task_mix, format_amount_compact
from shared.i18n import render_message
from shared.utils.user_error import safe_user_error_message


def format_amount(amount: float | int) -> str:
    """Format currency in Naira."""
    return format_naira(amount, decimal_places=2)


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
        {"amount": format_amount_compact(total_amount)},
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
        ✓ *Transaction Summary*

        *Transfer:* ₦100,000.00 to 2 recipients
          ✓ Mum - ₦50,000.00 (GT Bank •••1234)
          ✓ Tolu - ₦50,000.00 (Access Bank •••5678)

        ✓ *Airtime:* ₦1,000.00 for 08012345678 (MTN)

        *Total Spent:* ₦101,000.00

        _All transactions completed successfully_
    """
    task_types = [getattr(task, "type", "") for task in completed_tasks]
    header, footer = build_completion_frame(
        task_types=task_types,
        locale=locale,
        task_count=len(completed_tasks),
    )
    lines = [header, ""]
    total_spent = 0.0
    task_statuses = [
        _normalize_final_status(str(getattr(task, "payload", {}).get("final_status") or "success").lower())
        for task in completed_tasks
        if isinstance(getattr(task, "payload", None), dict)
    ]
    any_failed = any(status == "failed" for status in task_statuses)
    any_succeeded = any(status == "success" for status in task_statuses)
    any_processing = any(status == "processing" for status in task_statuses)
    task_mix = derive_task_mix(task_types)

    if any_processing:
        if task_mix == "transfer":
            header = "*Transfers Update*" if len(completed_tasks) > 1 else "*Transfer Update*"
        elif task_mix == "airtime":
            header = "*Airtime Update*"
        elif task_mix == "data":
            header = "*Data Update*"
        else:
            header = "*Transaction Update*"
        lines = [header, ""]

    # Group by task type
    transfer_tasks = [t for t in completed_tasks if t.type == "transfer"]
    airtime_tasks = [t for t in completed_tasks if t.type == "airtime"]
    data_tasks = [t for t in completed_tasks if t.type == "data"]
    other_tasks = [t for t in completed_tasks if t.type not in ["transfer", "airtime", "data"]]

    # Compact transfer lines
    if transfer_tasks:
        for task in transfer_tasks:
            recipients = task.payload.get("recipients", [])
            is_batch = task.payload.get("is_batch", False) or len(recipients) > 1

            if is_batch and recipients:
                for r in recipients:
                    amount = float(r.get("amount", 0) or 0)
                    total_spent += amount
                    recipient = (
                        format_summary_recipient_display_label(
                            r.get("recipient_name") or r.get("alias"),
                            r.get("recipient_resolved_name") or r.get("name"),
                        )
                        or render_message("transaction_summary.multi.recipient_unknown", locale)
                    )
                    bank = (
                        str(r.get("bank_name") or r.get("recipient_bank_name") or "").strip()
                        or render_message("transaction_summary.multi.bank_fallback", locale)
                    )
                    account = (
                        str(r.get("account") or r.get("recipient_account") or "").strip()
                        or render_message("transaction_summary.multi.account_fallback", locale)
                    )
                    status = _normalize_final_status(str(r.get("status", "success")).lower())
                    status_icon = _status_icon(status)
                    lines.append(
                        render_message(
                            "transaction_summary.multi.transfer_compact_line",
                            locale,
                            {
                                "icon": status_icon,
                                "amount": format_amount_compact(amount),
                                "recipient": recipient,
                                "bank": bank,
                                "account": account,
                            },
                        )
                    )
            else:
                amount = float(task.payload.get("amount", 0) or 0)
                status = _normalize_final_status(str(task.payload.get("final_status") or "success").lower())
                recipient = (
                    format_summary_recipient_display_label(
                        task.payload.get("recipient_name"),
                        task.payload.get("recipient_resolved_name"),
                    )
                    or render_message("transaction_summary.multi.recipient_fallback", locale)
                )
                bank = (
                    str(task.payload.get("recipient_bank_name") or "").strip()
                    or render_message("transaction_summary.multi.bank_fallback", locale)
                )
                account = (
                    str(task.payload.get("recipient_account") or "").strip()
                    or render_message("transaction_summary.multi.account_fallback", locale)
                )
                if status == "success":
                    total_spent += amount
                lines.append(
                    render_message(
                        "transaction_summary.multi.transfer_compact_line",
                        locale,
                        {
                            "icon": _status_icon(status),
                            "amount": format_amount_compact(amount),
                            "recipient": recipient,
                            "bank": bank,
                            "account": account,
                        },
                    )
                )
                if status == "failed" and (reason := _failure_reason(task.payload)):
                    lines.append(_format_failure_reason(reason, locale, task_type=task.type))

        lines.append("")

    # Handle airtime purchases
    if airtime_tasks:
        for task in airtime_tasks:
            amount = float(task.payload.get("amount", 0) or 0)
            status = _normalize_final_status(str(task.payload.get("final_status") or "success").lower())
            raw_phone = (
                task.payload.get("phone_number")
                or task.payload.get("recipient_phone")
                or task.payload.get("recipientPhone")
                or task.payload.get("phone")
            )
            phone = str(raw_phone).strip() if raw_phone else ""
            if not phone:
                phone = render_message("transaction_summary.multi.phone_fallback", locale)
            network = str(task.payload.get("network") or "")
            if status == "success":
                total_spent += amount
            icon = _status_icon(status)
            airtime_line = render_message(
                "transaction_summary.multi.airtime_line",
                locale,
                {"amount": format_amount_compact(amount), "phone": phone, "network": network},
            )
            lines.append(f"{icon} {airtime_line}")
            if status == "failed" and (reason := _failure_reason(task.payload)):
                lines.append(_format_failure_reason(reason, locale, task_type=task.type))
        lines.append("")

    # Handle data purchases
    if data_tasks:
        for task in data_tasks:
            amount = float(task.payload.get("amount", 0) or 0)
            status = _normalize_final_status(str(task.payload.get("final_status") or "success").lower())
            phone = task.payload.get("phone_number") or task.payload.get("target_phone") or render_message(
                "transaction_summary.multi.phone_fallback",
                locale,
            )
            plan = task.payload.get("plan_name") or render_message(
                "transaction_summary.multi.data_plan_fallback",
                locale,
            )
            if status == "success":
                total_spent += amount
            icon = _status_icon(status)
            data_line = render_message(
                "transaction_summary.multi.data_line",
                locale,
                {"plan": plan, "amount": format_amount_compact(amount), "phone": phone},
            )
            lines.append(f"{icon} {data_line}")
            if status == "failed" and (reason := _failure_reason(task.payload)):
                lines.append(_format_failure_reason(reason, locale, task_type=task.type))
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
    if len(completed_tasks) > 1 and total_spent > 0:
        lines.append(
            render_message(
                "transaction_summary.multi.total_spent",
                locale,
                {"amount": format_amount_compact(total_spent)},
            )
        )
        lines.append("")

    if any_processing:
        if any_succeeded and any_failed:
            footer = (
                "Some transactions completed, some failed, and others are still awaiting provider confirmation. "
                "You'll be notified when the final update arrives."
            )
        elif any_succeeded:
            footer = (
                "Some transactions completed successfully. Others are still awaiting provider confirmation. "
                "You'll be notified when the final update arrives."
            )
        elif any_failed:
            footer = (
                "Some transactions failed. Others are still awaiting provider confirmation. "
                "You'll be notified when the final update arrives."
            )
        else:
            footer = (
                "Some transactions are still awaiting provider confirmation. "
                "You'll be notified when the final update arrives."
            )
    elif any_failed:
        footer = "Some transactions completed, but others failed." if any_succeeded else "All transactions failed."

    lines.append(footer)

    return "\n".join(lines)


def _normalize_final_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"success", "successful", "confirmed", "completed"}:
        return "success"
    if normalized in {"pending", "processing", "queued"}:
        return "processing"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized or "success"


def _failure_reason(payload: dict[str, Any]) -> str | None:
    for key in ("error_message", "reason", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _format_failure_reason(reason: str, locale: str, *, task_type: str | None = None) -> str:
    safe_reason = safe_user_error_message(reason, task_type=task_type, locale=locale)
    return render_message("transaction_summary.multi.failure_reason", locale, {"reason": safe_reason})


def _status_icon(status: str) -> str:
    if status == "success":
        return "✓"
    if status == "processing":
        return "…"
    return "✗"


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
            {"amount": format_amount_compact(amount), "recipient": recipient},
        )
    elif task_type == "airtime":
        amount = payload.get("amount", 0)
        phone = payload.get("recipient_phone") or render_message("transaction_summary.intent.phone_fallback", locale)
        return render_message(
            "transaction_summary.intent.airtime",
            locale,
            {"amount": format_amount_compact(amount), "phone": phone},
        )
    elif task_type == "data":
        plan = payload.get("plan_name") or render_message("transaction_summary.intent.data_plan_fallback", locale)
        phone = payload.get("target_phone") or render_message("transaction_summary.intent.phone_fallback", locale)
        return render_message("transaction_summary.intent.data", locale, {"plan": plan, "phone": phone})
    return render_message("transaction_summary.intent.generic", locale, {"task_type": task_type.title()})

