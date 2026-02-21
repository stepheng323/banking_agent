"""Handler for transfer status and pending queries."""

from typing import Any

from apps.core.src.agent.graphs.support.models import SupportResponse
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_transfer_status(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle transfer_status intent.
    Confirms success or explains current state.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")
    created_at = transaction.get("created_at", "")

    # Parse timestamp for display
    time_str = ""
    if created_at:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            time_str = dt.strftime("%b %d at %I:%M %p")
        except Exception:
            time_str = ""

    if status == "success":
        message = render_message(
            "support.status.success_no_time",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient},
        )
        if time_str:
            message = render_message(
                "support.status.success_with_time",
                locale,
                {"amount": f"{amount:,.0f}", "recipient": recipient, "time": time_str},
            )
        return SupportResponse(
            message=message,
            offer_receipt=True,
            transaction_data=transaction,
        )

    elif status == "pending":
        message = render_message(
            "support.status.pending",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient},
        )
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    elif status == "failed":
        error = transaction.get("error_message", "")
        message = render_message(
            "support.status.failed",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient},
        )
        if error:
            message = f"{message}\n{render_message('support.common.reason', locale, {'reason': error})}"
        return SupportResponse(
            message=message,
            offer_retry=True,
            transaction_data=transaction,
        )

    else:
        return SupportResponse(
            message=render_message("support.status.raw_status", locale, {"status": status}),
            transaction_data=transaction,
        )


async def handle_pending(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle pending_transfer intent.
    Explains why transfer is stuck.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")

    if status == "pending":
        message = render_message(
            "support.pending.pending",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient},
        )
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    elif status == "success":
        message = render_message("support.pending.success_completed", locale)
        return SupportResponse(
            message=message,
            offer_receipt=True,
            transaction_data=transaction,
        )

    elif status == "failed":
        error = transaction.get("error_message", "")
        message = render_message("support.pending.failed_no_longer_pending", locale)
        if error:
            message = f"{message}\n{render_message('support.common.reason', locale, {'reason': error})}"
        return SupportResponse(
            message=message,
            offer_retry=True,
            transaction_data=transaction,
        )

    else:
        return SupportResponse(
            message=render_message("support.pending.raw_status", locale, {"status": status}),
            transaction_data=transaction,
        )
