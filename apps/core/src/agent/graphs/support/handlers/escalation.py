"""Handler for escalation - always creates a ticket.

This is the terminal handler for support issues that can't be resolved automatically.
Every escalation creates a ticket for accountability.
"""

from datetime import datetime
from typing import Any

from apps.core.src.agent.graphs.support.models import EscalationResult, SupportResponse
from shared.database.models import SupportTicketStatusEnum
from shared.services.ticket_service import TicketService
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
            summary = "User reported suspected fraud"
        elif reason == "max_attempts":
            summary = "Unable to resolve after multiple attempts"
        elif reason == "user_requested":
            summary = "User requested human support"
        else:
            summary = f"Support escalation: {reason or intent}"
    
    # Transaction reference for linking
    transaction_ref = None
    if transaction:
        transaction_ref = transaction.get("transaction_id") or str(transaction.get("id"))
    
    # Build details context
    details = {
        "reason": reason,
        "escalated_at": datetime.utcnow().isoformat(),
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
    ticket = ticket_service.create_ticket(
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
    
    # TODO: If notify_human, send Slack/email notification
    if notify_human:
        logger.info("support_notification_pending", ticket_code=ticket.ticket_code)
        # await notify_support_team(ticket)  # Future implementation
    
    # Build response message
    message = f"I've logged this for review.\n\n"
    message += f"**Ticket:** {ticket.ticket_code}\n\n"
    
    if reason == "fraud_suspected":
        message += "Our security team will prioritize this. "
        message += "You'll hear back within 1 hour."
    elif transaction_ref:
        message += "Our team will investigate and get back to you within 24 hours."
    else:
        message += "If you have the transaction reference, reply with it to speed things up."
    
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
) -> SupportResponse:
    """
    Generic escalation for system-triggered handoffs.
    Used when we can't resolve an issue automatically.
    """
    summary = f"System escalation: {reason}"
    if context:
        summary += f" - {context.get('message', '')}"
    
    return await handle_escalation(
        user_id=user_id,
        intent=intent,
        ticket_service=ticket_service,
        transaction=transaction,
        reason=reason,
        summary=summary,
        notify_human=True,
    )
