"""Handler for fraud suspected intent."""

from typing import Any

from apps.core.src.agent.graphs.support.models import EscalationResult, SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_fraud(transaction: dict[str, Any] | None) -> SupportResponse:
    """
    Handle fraud_suspected intent.
    ALWAYS escalates immediately - never provides operational details.
    """
    logger.warning(
        "fraud_suspected_triggered",
        transaction_id=transaction.get("id") if transaction else None,
    )

    message = "⚠ We're taking this seriously.\n"
    message += "A support agent will contact you shortly to investigate.\n"
    message += "Do not share any PINs or passwords."

    return SupportResponse(
        message=message,
        escalation=EscalationResult(
            reason="fraud_suspected",
            transaction_id=transaction.get("id") if transaction else None,
            context={
                "transaction": transaction,
                "priority": "high",
            },
        ),
        transaction_data=transaction,
    )
