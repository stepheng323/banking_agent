"""Handler for ticket status queries.

Users will ask:
- "Any update?"
- "What's happening with my case?"
- "Status of my ticket"
"""

from apps.chat.src.agent.workers.support.models import SupportResponse
from banking.presentation.i18n.renderer import render_message
from banking.support.services.ticket_service import TicketService
from shared.database.enums import SupportTicketStatusEnum
from shared.utils.logging import get_logger

logger = get_logger(__name__)


# Human-readable status message keys
STATUS_MESSAGE_KEYS = {
    SupportTicketStatusEnum.OPEN.value: "support.ticket.status_open",
    SupportTicketStatusEnum.IN_PROGRESS.value: "support.ticket.status_in_progress",
    SupportTicketStatusEnum.RESOLVED.value: "support.ticket.status_resolved",
    SupportTicketStatusEnum.CLOSED.value: "support.ticket.status_closed",
}


async def handle_ticket_status(
    user_id: str,
    ticket_service: TicketService,
    ticket_code: str | None = None,
    last_ticket_id: str | None = None,
    locale: str = "en",
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
        ticket = await ticket_service.get_ticket(ticket_code)
        if not ticket:
            return SupportResponse(
                message=render_message(
                    "support.ticket.not_found",
                    locale,
                    {"ticket_code": ticket_code},
                ),
            )

    # Priority 2: Last ticket from context
    elif last_ticket_id:
        ticket = await ticket_service.get_ticket(last_ticket_id)

    # Priority 3: Most recent open ticket
    if not ticket:
        ticket = await ticket_service.get_latest_ticket(user_id)

    if not ticket:
        return SupportResponse(
            message=render_message("support.ticket.none_open", locale),
        )

    # Build status response
    status_text = render_message(
        STATUS_MESSAGE_KEYS.get(ticket.status, "support.ticket.status_unknown"),
        locale,
    )

    # Format created_at
    created_str = (
        ticket.created_at.strftime("%b %d at %I:%M %p")
        if ticket.created_at
        else render_message("support.ticket.created_recently", locale)
    )

    message = render_message(
        "support.ticket.summary",
        locale,
        {"ticket_code": ticket.ticket_code, "status_text": status_text, "created": created_str},
    )

    # Add context based on status
    if ticket.status == SupportTicketStatusEnum.OPEN.value:
        message = f"{message}{render_message('support.ticket.open_hint', locale)}"
    elif ticket.status == SupportTicketStatusEnum.IN_PROGRESS.value:
        message = f"{message}{render_message('support.ticket.in_progress_hint', locale)}"
    elif ticket.status == SupportTicketStatusEnum.RESOLVED.value:
        if ticket.resolved_at:
            resolved_str = ticket.resolved_at.strftime("%b %d at %I:%M %p")
            message = f"{message}{render_message('support.ticket.resolved_on', locale, {'resolved': resolved_str})}"

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
