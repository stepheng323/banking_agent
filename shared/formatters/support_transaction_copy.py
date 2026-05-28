"""Support workflow transaction copy."""

from __future__ import annotations

from typing import Any

from shared.formatters.transaction_copy_common import _format_support_amount_value, _parse_support_time
from shared.i18n.renderer import render_message


def format_support_transfer_status_sentence(
    transaction: dict[str, Any],
    *,
    status: str,
    locale: str,
) -> str:
    """Build support's compact transfer status/detail sentence."""
    amount = _format_support_amount_value(transaction.get("amount"))
    recipient = transaction.get("recipient_name") or "recipient"

    if status == "successful":
        time_str = _parse_support_time(transaction.get("created_at"))
        if time_str:
            return render_message(
                "support.status.success_with_time",
                locale,
                {"amount": amount, "recipient": recipient, "time": time_str},
            )
        return render_message(
            "support.status.success_no_time",
            locale,
            {"amount": amount, "recipient": recipient},
        )

    if status in {"pending", "processing"}:
        return render_message(
            "support.status.pending",
            locale,
            {"amount": amount, "recipient": recipient},
        )

    if status == "failed":
        return render_message(
            "support.status.failed",
            locale,
            {"amount": amount, "recipient": recipient},
        )

    return render_message("support.status.raw_status", locale, {"status": status})
