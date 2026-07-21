"""Compact transaction intent-line formatting."""

from __future__ import annotations

from typing import Any

from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.presentation.i18n.renderer import render_message
from shared.money import to_naira


def format_intent_line(task_type: str, payload: dict[str, Any], locale: str = "en") -> str:
    """Generate a precise intent string for a task."""
    if task_type == "transfer":
        amount = to_naira(payload.get("amount"))
        if amount is None or amount <= 0:
            return ""
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
    if task_type == "airtime":
        amount = to_naira(payload.get("amount"))
        if amount is None or amount <= 0:
            return ""
        phone = payload.get("recipient_phone") or render_message("transaction_summary.intent.phone_fallback", locale)
        return render_message(
            "transaction_summary.intent.airtime",
            locale,
            {"amount": format_amount_compact(amount), "phone": phone},
        )
    if task_type == "data":
        plan = payload.get("plan_name") or render_message("transaction_summary.intent.data_plan_fallback", locale)
        phone = payload.get("target_phone") or render_message("transaction_summary.intent.phone_fallback", locale)
        return render_message("transaction_summary.intent.data", locale, {"plan": plan, "phone": phone})
    return render_message("transaction_summary.intent.generic", locale, {"task_type": task_type.title()})
