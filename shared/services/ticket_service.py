"""Service for creating and managing support tickets."""

from datetime import datetime
from typing import Any

from shared.database.enums import (
    SupportTicketPriorityEnum,
    SupportTicketStatusEnum,
)
from shared.database.models import SupportTicket
from shared.repositories.support_ticket_repository import SupportTicketRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


# Intent to priority mapping
INTENT_PRIORITY: dict[str, SupportTicketPriorityEnum] = {
    "fraud_report": SupportTicketPriorityEnum.URGENT,
    "fraud_suspected": SupportTicketPriorityEnum.URGENT,
    "wrong_recipient": SupportTicketPriorityEnum.HIGH,
    "reversal_refund": SupportTicketPriorityEnum.HIGH,
    "failed_transfer": SupportTicketPriorityEnum.MEDIUM,
    "pending_transfer": SupportTicketPriorityEnum.MEDIUM,
    "general_tx_issue": SupportTicketPriorityEnum.MEDIUM,
    "human_handoff": SupportTicketPriorityEnum.MEDIUM,
    "receipt_request": SupportTicketPriorityEnum.LOW,
    "limits_fees": SupportTicketPriorityEnum.LOW,
    "account_linking": SupportTicketPriorityEnum.LOW,
}


class TicketService:
    """Service for support ticket operations."""

    def __init__(self, ticket_repo: SupportTicketRepository):
        self.repo = ticket_repo

    async def create_ticket(
        self,
        user_id: str,
        intent: str,
        summary: str,
        transaction_ref: str | None = None,
        details: dict[str, Any] | None = None,
        channel: str = "whatsapp",
    ) -> SupportTicket:
        """
        Create a new support ticket.

        Args:
            user_id: User's UUID
            intent: Support intent (from SupportIntent enum value)
            summary: Brief description of the issue
            transaction_ref: Optional transaction reference
            details: Optional additional context (JSON)
            channel: Channel source (default: whatsapp)

        Returns:
            Created SupportTicket
        """
        ticket_code = await self.repo.generate_ticket_code()

        priority = INTENT_PRIORITY.get(intent, SupportTicketPriorityEnum.MEDIUM)

        ticket = await self.repo.create(
            ticket_code=ticket_code,
            user_id=user_id,
            channel=channel,
            intent=intent,
            status=SupportTicketStatusEnum.OPEN.value,
            priority=priority.value,
            transaction_ref=transaction_ref,
            summary=summary,
            details=details or {},
        )

        logger.info(
            "support_ticket_created",
            ticket_code=ticket_code,
            user_id=user_id,
            intent=intent,
            priority=priority.value,
            has_transaction=bool(transaction_ref),
        )

        return ticket

    async def get_ticket(self, ticket_code: str) -> SupportTicket | None:
        """Get a ticket by its code."""
        return await self.repo.get_by_ticket_code(ticket_code)

    async def get_user_open_tickets(self, user_id: str) -> list[SupportTicket]:
        """Get all open tickets for a user."""
        return await self.repo.get_open_tickets(user_id)

    async def get_latest_ticket(self, user_id: str) -> SupportTicket | None:
        """Get the most recent open ticket for a user."""
        return await self.repo.get_latest_open(user_id)

    async def update_status(
        self,
        ticket_code: str,
        status: SupportTicketStatusEnum,
    ) -> SupportTicket | None:
        """Update ticket status."""
        ticket = await self.repo.get_by_ticket_code(ticket_code)
        if not ticket:
            return None

        ticket = await self.repo.update(ticket, status=status.value)

        if status == SupportTicketStatusEnum.RESOLVED:
            ticket = await self.repo.update(ticket, resolved_at=datetime.utcnow())

        logger.info(
            "support_ticket_status_updated",
            ticket_code=ticket_code,
            new_status=status.value,
        )

        return ticket

    async def resolve_ticket(self, ticket_code: str) -> SupportTicket | None:
        """Mark a ticket as resolved."""
        return await self.update_status(ticket_code, SupportTicketStatusEnum.RESOLVED)

    async def close_ticket(self, ticket_code: str) -> SupportTicket | None:
        """Close a ticket."""
        return await self.update_status(ticket_code, SupportTicketStatusEnum.CLOSED)
