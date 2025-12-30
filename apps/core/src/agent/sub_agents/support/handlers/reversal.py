"""Handler for reversal/refund status queries."""

from typing import Any

from apps.core.src.agent.sub_agents.support.models import EscalationResult, SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_reversal_status(transaction: dict[str, Any]) -> SupportResponse:
    """
    Handle reversal_refund_status intent.
    Explains refund status - never promises timelines unless explicitly known.
    """
    status = transaction.get("status", "unknown")
    amount = transaction.get("amount", 0)
    provider_response = transaction.get("provider_response", {})

    if status == "success":
        return SupportResponse(
            message="• This transfer was successful. No reversal is applicable.",
            offer_receipt=True,
            transaction_data=transaction,
        )

    if status == "reversed":
        message = f"• Your ₦{amount:,.0f} has been reversed to your account."
        return SupportResponse(
            message=message,
            transaction_data=transaction,
        )

    if status == "failed":
        was_debited = provider_response.get("debited", False)
        reversal_status = provider_response.get("reversal_status", "")

        if not was_debited:
            message = "No money was debited, so no refund is needed."
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )

        if reversal_status == "completed":
            message = f"• The ₦{amount:,.0f} refund has been completed."
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )

        if reversal_status == "processing":
            message = f"○ The ₦{amount:,.0f} reversal is being processed.\n"
            message += "Refunds typically return to your account within 24-48 hours."
            return SupportResponse(
                message=message,
                transaction_data=transaction,
            )

        # No explicit reversal status - escalate
        message = f"⚠ You were debited ₦{amount:,.0f} and a reversal is needed.\n"
        message += "Escalating this to our support team."
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
        message="Unable to determine refund status.",
        escalation=EscalationResult(reason="unknown_reversal_status", transaction_id=transaction.get("id")),
        transaction_data=transaction,
    )
