"""Handler for reversal/refund status queries."""

from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.support.handlers.status_utils import resolve_transaction_status
from banking.support.models import EscalationResult, SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_reversal_status(transaction: dict[str, Any], *, locale: str = "en") -> SupportResponse:
    """
    Handle reversal_refund_status intent.
    Explains refund status - never promises timelines unless explicitly known.
    """
    status = resolve_transaction_status(transaction)
    amount = transaction.get("amount", 0)
    provider_response = transaction.get("provider_response", {})

    if status == "successful":
        return SupportResponse(
            message=render_message("support.reversal.success_no_reversal", locale),
            offer_receipt=True,
            transaction_data=transaction,
        )

    if status == "reversed":
        message = render_message("support.reversal.reversed", locale, {"amount": f"{amount:,.0f}"})
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    if status == "failed":
        was_debited = provider_response.get("debited", False)
        reversal_status = provider_response.get("reversal_status", "")

        if not was_debited:
            message = render_message("support.reversal.no_refund_needed", locale)
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )

        if reversal_status == "completed":
            message = render_message("support.reversal.completed", locale, {"amount": f"{amount:,.0f}"})
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )

        if reversal_status == "processing":
            message = render_message("support.reversal.processing", locale, {"amount": f"{amount:,.0f}"})
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )

        # No explicit reversal status - escalate
        message = render_message("support.reversal.needs_escalation", locale, {"amount": f"{amount:,.0f}"})
        return SupportResponse(
            message=message,
            escalation=EscalationResult(
                reason="reversal_needed",
                transaction_id=transaction.get("id"),
                context={"amount": amount, "was_debited": True},
            ),
            transaction_data=transaction,
        )

    return SupportResponse(
        message=render_message("support.reversal.unknown", locale),
        escalation=EscalationResult(reason="unknown_reversal_status", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )
