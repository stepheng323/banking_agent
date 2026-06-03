"""Handler for escalation - always creates a ticket.

This is the terminal handler for support issues that can't be resolved automatically.
Every escalation creates a ticket for accountability.
"""

from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.support.models import EscalationResult, SupportResponse
from banking.support.services.ticket_service import TicketService
from shared.utils.datetime import utc_now_naive
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def handle_escalation(
    user_id: str,
    intent: str,
    ticket_service: TicketService,
    transaction: dict[str, Any] | None = None,
    reason: str = "",
    summary: str | None = None,
    notify_human: bool = False,
    locale: str = "en",
) -> SupportResponse:
    """
    Handle escalation by creating a ticket.

    Args:
        user_id: User's UUID
        intent: Support intent that triggered escalation
        ticket_service: TicketService instance
        transaction: Transaction context if available
        reason: Why we're escalating
        summary: Brief description (auto-generated if not provided)
        notify_human: Whether to alert support team immediately

    Returns:
        SupportResponse with ticket info
    """
    # Auto-generate summary if not provided
    if not summary:
        if reason == "fraud_suspected":
            summary = render_message("support.escalation.summary_fraud_suspected", locale)
        elif reason == "max_attempts":
            summary = render_message("support.escalation.summary_max_attempts", locale)
        elif reason == "user_requested":
            summary = render_message("support.escalation.summary_user_requested", locale)
        else:
            summary = render_message(
                "support.escalation.summary_default",
                locale,
                {"reason_or_intent": reason or intent},
            )

    # Transaction reference for linking
    transaction_ref = None
    if transaction:
        transaction_ref = transaction.get("transaction_id") or str(transaction.get("id"))

    # Build details context
    details = {
        "reason": reason,
        "escalated_at": utc_now_naive().isoformat(),
        "notify_human": notify_human,
    }
    if transaction:
        details["transaction"] = {
            "id": str(transaction.get("id")),
            "status": transaction.get("status"),
            "amount": transaction.get("amount"),
            "recipient_name": transaction.get("recipient_name"),
        }

    # Create ticket
    ticket = await ticket_service.create_ticket(
        user_id=user_id,
        intent=intent,
        summary=summary,
        transaction_ref=transaction_ref,
        details=details,
    )

    logger.info(
        "escalation_ticket_created",
        ticket_code=ticket.ticket_code,
        user_id=user_id,
        intent=intent,
        reason=reason,
        notify_human=notify_human,
    )

    if notify_human:
        logger.info("support_notification_pending", ticket_code=ticket.ticket_code)

    message = render_message("support.escalation.logged", locale)
    message += render_message("support.escalation.ticket", locale, {"ticket_code": ticket.ticket_code})

    if reason == "fraud_suspected":
        message += render_message("support.escalation.fraud_priority", locale)
    elif transaction_ref:
        message += render_message("support.escalation.with_transaction_ref", locale)
    else:
        message += render_message("support.escalation.ask_transaction_ref", locale)

    return SupportResponse(
        message=message,
        escalation=EscalationResult(
            reason=reason or "escalation",
            transaction_id=transaction_ref,
            context={"ticket_code": ticket.ticket_code},
        ),
        transaction_data=transaction,
        next_step="ticket_created",
    )


async def handle_generic_escalation(
    user_id: str,
    reason: str,
    ticket_service: TicketService,
    intent: str = "general_tx_issue",
    transaction: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    locale: str = "en",
) -> SupportResponse:
    """
    Generic escalation for system-triggered handoffs.
    Used when we can't resolve an issue automatically.
    """
    summary = render_message("support.escalation.summary_system", locale, {"reason": reason})
    if context:
        summary += render_message(
            "support.escalation.summary_context_suffix",
            locale,
            {"message": context.get("message", "")},
        )

    return await handle_escalation(
        user_id=user_id,
        intent=intent,
        ticket_service=ticket_service,
        transaction=transaction,
        reason=reason,
        summary=summary,
        notify_human=True,
        locale=locale,
    )
