"""Handler for ticket status queries.

Users will ask:
- "Any update?"
- "What's happening with my case?"
- "Status of my ticket"
"""

from typing import Any

from apps.core.src.agent.graphs.support.models import EscalationResult, SupportResponse
from shared.database.enums import SupportTicketStatusEnum
from shared.services.ticket_service import TicketService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


# Human-readable status messages
STATUS_MESSAGES = {
    SupportTicketStatusEnum.OPEN.value: "Your case is open and queued for review.",
    SupportTicketStatusEnum.IN_PROGRESS.value: "Our team is actively working on this.",
    SupportTicketStatusEnum.RESOLVED.value: "This case has been resolved.",
    SupportTicketStatusEnum.CLOSED.value: "This case has been closed.",
}


async def handle_ticket_status(
    user_id: str,
    ticket_service: TicketService,
    ticket_code: str | None = None,
    last_ticket_id: str | None = None,
) -> SupportResponse:
    """
    Handle ticket status queries.
    
    Resolution priority:
    1. Explicit ticket_code if provided
    2. last_ticket_id from context
    3. Most recent open ticket for user
    
    Args:
        user_id: User's UUID
        ticket_service: TicketService instance
        ticket_code: Explicit ticket code mentioned by user
        last_ticket_id: Last ticket from SupportContext
    
    Returns:
        SupportResponse with ticket status
    """
    ticket = None
    
    # Priority 1: Explicit ticket code
    if ticket_code:
        ticket = ticket_service.get_ticket(ticket_code)
        if not ticket:
            return SupportResponse(
                message=f"I couldn't find a ticket with code {ticket_code}.\n\n"
                        "Please check the code and try again.",
            )
    
    # Priority 2: Last ticket from context
    elif last_ticket_id:
        ticket = ticket_service.get_ticket(last_ticket_id)
    
    # Priority 3: Most recent open ticket
    if not ticket:
        ticket = ticket_service.get_latest_ticket(user_id)
    
    if not ticket:
        return SupportResponse(
            message="I don't see any open support tickets for you.\n\n"
                    "Is there something I can help you with?",
        )
    
    # Build status response
    status_text = STATUS_MESSAGES.get(ticket.status, "Status unknown")
    
    # Format created_at
    created_str = ticket.created_at.strftime("%b %d at %I:%M %p") if ticket.created_at else "recently"
    
    message = f"**Ticket:** {ticket.ticket_code}\n"
    message += f"**Status:** {status_text}\n"
    message += f"**Created:** {created_str}\n\n"
    
    # Add context based on status
    if ticket.status == SupportTicketStatusEnum.OPEN.value:
        message += "Our team typically responds within 24 hours."
    elif ticket.status == SupportTicketStatusEnum.IN_PROGRESS.value:
        message += "You'll receive an update once we have more information."
    elif ticket.status == SupportTicketStatusEnum.RESOLVED.value:
        if ticket.resolved_at:
            resolved_str = ticket.resolved_at.strftime("%b %d at %I:%M %p")
            message += f"Resolved on {resolved_str}."
    
    logger.info(
        "ticket_status_queried",
        ticket_code=ticket.ticket_code,
        status=ticket.status,
        user_id=user_id,
    )
    
    return SupportResponse(
        message=message,
        transaction_data={"ticket_code": ticket.ticket_code, "status": ticket.status},
    )


async def handle_any_update(
    user_id: str,
    ticket_service: TicketService,
    last_ticket_id: str | None = None,
    last_transaction_ref: str | None = None,
) -> SupportResponse:
    """
    Handle "any update?" queries.
    
    This is a common follow-up after ticket creation.
    Falls back to showing the most recent ticket.
    """
    # Check for ticket first
    if last_ticket_id:
        return await handle_ticket_status(
            user_id=user_id,
            ticket_service=ticket_service,
            last_ticket_id=last_ticket_id,
        )
    
    # Check for recent open tickets
    open_tickets = ticket_service.get_user_open_tickets(user_id)
    
    if open_tickets:
        ticket = open_tickets[0]  # Most recent
        return await handle_ticket_status(
            user_id=user_id,
            ticket_service=ticket_service,
            ticket_code=ticket.ticket_code,
        )
    
    # No tickets found
    return SupportResponse(
        message="I don't have any pending updates for you.\n\n"
                "Is there something specific I can help with?",
    )
