"""Handler for failure reason and wrong debit queries."""

from typing import Any

from apps.core.src.agent.graphs.support.models import EscalationResult, SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_failure_reason(transaction: dict[str, Any]) -> SupportResponse:
    """
    Handle transfer_failure_reason intent.
    Explains why a transfer failed using provider data.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    error = transaction.get("error_message", "")
    provider_response = transaction.get("provider_response", {})

    # Extract provider error if available
    provider_error = provider_response.get("error_code", "") or provider_response.get("reason", "")

    if status == "success":
        return SupportResponse(
            message="• This transfer was actually successful. No failure occurred.",
            offer_receipt=True,
            transaction_data=transaction,
        )

    if status == "failed":
        # Build explanation from available error info
        if error:
            reason = error
        elif provider_error:
            reason = provider_error
        else:
            reason = "The bank did not provide a specific reason."

        # Check if money was debited
        was_debited = provider_response.get("debited", False)

        if was_debited:
            message = f"× This ₦{amount:,.0f} transfer failed after your account was debited.\n"
            message += f"Reason: {reason}\n"
            message += "A refund is being processed."
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )
        else:
            message = f"× This ₦{amount:,.0f} transfer failed.\n"
            message += f"Reason: {reason}\n"
            message += "No money was debited from your account."
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )

    return SupportResponse(
        message="Unable to determine failure reason. Please contact support.",
        escalation=EscalationResult(reason="unknown_error", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )


async def handle_wrong_debit(transaction: dict[str, Any]) -> SupportResponse:
    """
    Handle wrong_debit intent.
    Debited but transfer didn't complete.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    provider_response = transaction.get("provider_response", {})

    if status == "success":
        return SupportResponse(
            message="• This transfer was successful. The debit was correct.",
            offer_receipt=True,
            transaction_data=transaction,
        )

    if status == "failed":
        was_debited = provider_response.get("debited", False)

        if was_debited:
            message = f"⚠ Your account was debited ₦{amount:,.0f}, but the transfer did not complete.\n"
            message += "This is being reversed automatically.\n"
            message += "Current status: Reversal in progress"
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )
        else:
            message = "× The transfer failed, but no money was debited from your account."
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )

    if status == "pending":
        message = "○ This transfer is still processing.\n"
        message += "If it fails, any debit will be reversed automatically."
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    return SupportResponse(
        message="We're investigating this debit issue.",
        escalation=EscalationResult(reason="wrong_debit", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )
