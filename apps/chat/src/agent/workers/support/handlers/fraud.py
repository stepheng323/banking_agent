"""Handler for fraud suspected intent."""

from typing import Any

from apps.chat.src.agent.workers.support.models import EscalationResult, SupportResponse
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_fraud(transaction: dict[str, Any] | None, *, locale: str = "en") -> SupportResponse:
    """
    Handle fraud_suspected intent.
    ALWAYS escalates immediately - never provides operational details.
    """
    logger.warning(
        "fraud_suspected_triggered",
        transaction_id=transaction.get("id") if transaction else None,
    )

    message = render_message("support.fraud.alert", locale)

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
