"""Handler for retry transfer requests."""

from typing import Any

from apps.core.src.agent.graphs.support.models import SupportResponse
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _normalize_status(status: str) -> str:
    status = (status or "unknown").strip().lower()
    if status == "success":
        return "successful"
    return status


async def handle_retry(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle retry_transfer intent.
    Checks if retryable and prepares for TransferFlowGraph hydration.
    """
    status = _normalize_status(str(transaction.get("status", "unknown")))
    amount = transaction.get("amount", 0)
    recipient = transaction.get("recipient_name", "recipient")

    if status == "successful":
        message = render_message(
            "support.retry.already_success",
            locale,
            {"amount": f"{amount:,.0f}", "recipient": recipient},
        )
        return SupportResponse(
            message=message,
            offer_retry=True,  # Actually means "send again"
            transaction_data=transaction,
        )

    if status in {"pending", "processing"}:
        message = render_message("support.retry.pending_wait", locale)
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
            message = render_message(
                "support.retry.ready",
                locale,
                {"amount": f"{amount:,.0f}", "recipient": recipient},
            )
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )
        else:
            message = render_message("support.retry.not_retryable", locale, {"error": error})
            return SupportResponse(
                message=message,
                offer_retry=False,
                transaction_data=transaction,
            )

    return SupportResponse(
        message=render_message("support.retry.unknown_status", locale, {"status": status}),
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
