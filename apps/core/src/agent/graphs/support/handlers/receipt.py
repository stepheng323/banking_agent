"""Handler for receipt requests."""

from typing import Any

from apps.core.src.agent.graphs.support.models import SupportResponse
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_receipt_request(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle receipt_request intent.
    Only provides receipt for successful transactions.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")
    tx_id = transaction.get("transaction_id", "")
    created_at = transaction.get("created_at", "")

    if status != "success":
        message = render_message("support.receipt.unavailable_for_status", locale, {"status": status})
        return SupportResponse(
            message=message,
            offer_receipt=False,
            transaction_data=transaction,
        )

    # Format timestamp
    time_str = ""
    if created_at:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            time_str = dt.strftime("%b %d, %Y at %I:%M %p")
        except Exception:
            time_str = created_at[:10] if created_at else ""

    # Build receipt message
    receipt = f"{render_message('support.receipt.title', locale)}\n"
    receipt += f"{render_message('support.receipt.divider', locale)}\n"
    receipt += render_message("support.receipt.amount", locale, {"amount": f"{amount:,.2f}"}) + "\n"
    receipt += render_message("support.receipt.to", locale, {"recipient": recipient}) + "\n"
    receipt += (
        render_message(
            "support.receipt.bank",
            locale,
            {"bank": transaction.get("recipient_bank_name", "")},
        )
        + "\n"
    )
    receipt += (
        render_message(
            "support.receipt.account",
            locale,
            {"account": transaction.get("recipient_account_number", "")},
        )
        + "\n"
    )
    if tx_id:
        receipt += render_message("support.receipt.ref", locale, {"reference": tx_id}) + "\n"
    if time_str:
        receipt += render_message("support.receipt.date", locale, {"date": time_str}) + "\n"
    receipt += render_message("support.receipt.status_success", locale)

    return SupportResponse(
        message=receipt,
        offer_receipt=False,  # Already providing it
        transaction_data=transaction,
    )
