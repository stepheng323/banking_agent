"""Handler for failure reason and wrong debit queries."""

from typing import Any

from apps.core.src.agent.graphs.support.models import EscalationResult, SupportResponse
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _normalize_status(status: str) -> str:
    status = (status or "unknown").strip().lower()
    if status == "success":
        return "successful"
    return status


async def handle_failure_reason(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle transfer_failure_reason intent.
    Explains why a transfer failed using provider data.
    """
    status = _normalize_status(str(transaction.get("status", "unknown")))
    amount = transaction.get("amount", 0)
    error = transaction.get("error_message", "")
    provider_response = transaction.get("provider_response", {})

    # Extract provider error if available
    provider_error = (
        provider_response.get("reason", "")
        or provider_response.get("message", "")
        or provider_response.get("response_message", "")
        or provider_response.get("response_code", "")
        or provider_response.get("error_code", "")
    )

    if status == "successful":
        return SupportResponse(
            message=render_message("support.failure.success_no_failure", locale),
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
            reason = render_message("support.failure.reason_not_provided", locale)

        # Check if money was debited
        was_debited = provider_response.get("debited", False)

        if was_debited:
            message = render_message(
                "support.failure.debited_failed",
                locale,
                {"amount": f"{amount:,.0f}", "reason": reason},
            )
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )
        else:
            message = render_message(
                "support.failure.not_debited_failed",
                locale,
                {"amount": f"{amount:,.0f}", "reason": reason},
            )
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )

    return SupportResponse(
        message=render_message("support.failure.unknown_reason_contact", locale),
        escalation=EscalationResult(reason="unknown_error", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )


async def handle_wrong_debit(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle wrong_debit intent.
    Debited but transfer didn't complete.
    """
    status = _normalize_status(str(transaction.get("status", "unknown")))
    amount = transaction.get("amount", 0)
    provider_response = transaction.get("provider_response", {})

    if status == "successful":
        return SupportResponse(
            message=render_message("support.wrong_debit.success_debit_correct", locale),
            offer_receipt=True,
            transaction_data=transaction,
        )

    if status == "failed":
        was_debited = provider_response.get("debited", False)

        if was_debited:
            message = render_message(
                "support.wrong_debit.failed_but_debited",
                locale,
                {"amount": f"{amount:,.0f}"},
            )
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )
        else:
            message = render_message("support.wrong_debit.failed_not_debited", locale)
            return SupportResponse(
                message=message,
                offer_retry=True,
                transaction_data=transaction,
            )

    if status in {"pending", "processing"}:
        message = render_message("support.wrong_debit.pending_processing", locale)
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    return SupportResponse(
        message=render_message("support.wrong_debit.investigating", locale),
        escalation=EscalationResult(reason="wrong_debit", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )
