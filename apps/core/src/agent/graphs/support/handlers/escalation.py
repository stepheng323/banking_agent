"""Handler for explicit escalation requests."""

from typing import Any

from apps.core.src.agent.graphs.support.models import EscalationResult, SupportResponse
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_escalation(transaction: dict[str, Any] | None, reason: str = "") -> SupportResponse:
    """
    Handle support_escalation intent.
    User explicitly requests human help.
    """
    logger.info(
        "explicit_escalation_requested",
        transaction_id=transaction.get("id") if transaction else None,
        reason=reason,
    )

    message = "I understand. Connecting you to our support team.\n"
    message += "Someone will respond within a few minutes."

    return SupportResponse(
        message=message,
        escalation=EscalationResult(
            reason=reason or "user_requested",
            transaction_id=transaction.get("id") if transaction else None,
            context={"transaction": transaction} if transaction else {},
        ),
        transaction_data=transaction,
    )


async def handle_generic_escalation(
    reason: str,
    transaction: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> SupportResponse:
    """
    Generic escalation for system-triggered handoffs.
    Used when we can't resolve an issue automatically.
    """
    logger.info("system_escalation", reason=reason)

    message = "I'm unable to resolve this automatically.\n"
    message += "Escalating to our support team for you."

    return SupportResponse(
        message=message,
        escalation=EscalationResult(
            reason=reason,
            transaction_id=transaction.get("id") if transaction else None,
            context=context or {},
        ),
        transaction_data=transaction,
    )
