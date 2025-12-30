"""Handler for receipt requests."""

from typing import Any

from apps.core.src.agent.sub_agents.support.models import SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_receipt_request(transaction: dict[str, Any]) -> SupportResponse:
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
        message = f"× Cannot provide receipt. This transfer {status}."
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
    receipt = "*Transfer Receipt*\n"
    receipt += "─────────────────\n"
    receipt += f"Amount: ₦{amount:,.2f}\n"
    receipt += f"To: {recipient}\n"
    receipt += f"Bank: {transaction.get('recipient_bank_name', '')}\n"
    receipt += f"Account: {transaction.get('recipient_account_number', '')}\n"
    if tx_id:
        receipt += f"Ref: {tx_id}\n"
    if time_str:
        receipt += f"Date: {time_str}\n"
    receipt += "Status: • Successful"

    return SupportResponse(
        message=receipt,
        offer_receipt=False,  # Already providing it
        transaction_data=transaction,
    )
