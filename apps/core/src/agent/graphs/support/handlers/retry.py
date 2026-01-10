"""Handler for retry transfer requests."""

from typing import Any

from apps.core.src.agent.graphs.support.models import SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_retry(transaction: dict[str, Any]) -> SupportResponse:
    """
    Handle retry_transfer intent.
    Checks if retryable and prepares for TransferFlowGraph hydration.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")

    if status == "success":
        message = "• This transfer was already successful.\n"
        message += f"Would you like to send another ₦{amount:,.0f} to {recipient}?"
        return SupportResponse(
            message=message,
            offer_retry=True,  # Actually means "send again"
            transaction_data=transaction,
        )

    if status == "pending":
        message = "○ This transfer is still processing.\n"
        message += "Please wait for it to complete before retrying."
        return SupportResponse(
            message=message,
            offer_retry=False,
            transaction_data=transaction,
        )

    if status == "failed":
        # Check if retryable based on error
        error = transaction.get("error_message", "")
        non_retryable_errors = ["insufficient funds", "account blocked", "limit exceeded"]

        is_retryable = not any(e in error.lower() for e in non_retryable_errors)

        if is_retryable:
            message = f"Ready to retry ₦{amount:,.0f} to {recipient}.\n"
            message += "Reply 'yes' to confirm."
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )
        else:
            message = f"× Cannot retry this transfer: {error}\n"
            message += "Please resolve the issue first."
            return SupportResponse(
                message=message,
                offer_retry=False,
                transaction_data=transaction,
            )

    return SupportResponse(
        message=f"Cannot determine if this transfer can be retried. Status: {status}",
        transaction_data=transaction,
    )


def build_retry_quoted_data(transaction: dict[str, Any]) -> dict[str, Any]:
    """
    Build quoted_data for hydrating TransferFlowGraph.
    This matches the expected format from ActionableMessage.
    """
    return {
        "type": "retry_transfer",
        "data": {
            "amount": transaction.get("amount"),
            "recipient_name": transaction.get("recipient_name"),
            "recipient_account_number": transaction.get("recipient_account_number"),
            "recipient_bank_code": transaction.get("recipient_bank_code"),
            "recipient_bank_name": transaction.get("recipient_bank_name"),
            "narration": transaction.get("narration"),
            "source_bank_name": transaction.get("source_bank_name"),
        },
    }
