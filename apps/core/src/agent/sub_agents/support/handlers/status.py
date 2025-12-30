"""Handler for transfer status and pending queries."""

from typing import Any

from apps.core.src.agent.sub_agents.support.models import SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_transfer_status(transaction: dict[str, Any]) -> SupportResponse:
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
        message = f"• This transfer of ₦{amount:,.0f} to {recipient} was successful"
        if time_str:
            message += f" on {time_str}."
        else:
            message += "."
        return SupportResponse(
            message=message,
            offer_receipt=True,
            transaction_data=transaction,
        )

    elif status == "pending":
        message = f"○ This transfer of ₦{amount:,.0f} to {recipient} is still pending.\n"
        message += "We're awaiting confirmation from the bank."
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    elif status == "failed":
        error = transaction.get("error_message", "")
        message = f"× This transfer of ₦{amount:,.0f} to {recipient} failed."
        if error:
            message += f"\nReason: {error}"
        return SupportResponse(
            message=message,
            offer_retry=True,
            transaction_data=transaction,
        )

    else:
        return SupportResponse(
            message=f"Transfer status: {status}",
            transaction_data=transaction,
        )


async def handle_pending(transaction: dict[str, Any]) -> SupportResponse:
    """
    Handle pending_transfer intent.
    Explains why transfer is stuck.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")

    if status == "pending":
        message = f"○ Your transfer of ₦{amount:,.0f} to {recipient} is still being processed.\n"
        message += "Bank confirmations can take a few minutes."
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    elif status == "success":
        message = "• Good news! This transfer actually completed successfully."
        return SupportResponse(
            message=message,
            offer_receipt=True,
            transaction_data=transaction,
        )

    elif status == "failed":
        error = transaction.get("error_message", "")
        message = "× This transfer failed and is no longer pending."
        if error:
            message += f"\nReason: {error}"
        return SupportResponse(
            message=message,
            offer_retry=True,
            transaction_data=transaction,
        )

    else:
        return SupportResponse(
            message=f"Current status: {status}",
            transaction_data=transaction,
        )
