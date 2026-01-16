"""Repository for SupportTicket model."""

from datetime import date
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from shared.database.enums import SupportTicketStatusEnum
from shared.database.models import SupportTicket
from shared.repositories.base import BaseRepository


class SupportTicketRepository(BaseRepository[SupportTicket]):
    """Repository for SupportTicket operations."""

    def __init__(self, db: Session):
        super().__init__(db, SupportTicket)

    def get_by_ticket_code(self, ticket_code: str) -> SupportTicket | None:
        """Get a ticket by its code (e.g., SUP-20260116-0042)."""
        return (
            self.db.query(SupportTicket)
            .filter(SupportTicket.ticket_code == ticket_code)
            .first()
        )

    def get_by_user(
        self, user_id: str, limit: int = 20, include_closed: bool = False
    ) -> list[SupportTicket]:
        """Get all tickets for a user, ordered by created_at descending."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        query = (
            self.db.query(SupportTicket)
            .filter(SupportTicket.user_id == lookup_id)
        )
        
        if not include_closed:
            query = query.filter(
                SupportTicket.status != SupportTicketStatusEnum.CLOSED.value
            )
        
        return query.order_by(SupportTicket.created_at.desc()).limit(limit).all()

    def get_open_tickets(self, user_id: str) -> list[SupportTicket]:
        """Get open tickets for a user."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        return (
            self.db.query(SupportTicket)
            .filter(
                SupportTicket.user_id == lookup_id,
                SupportTicket.status.in_([
                    SupportTicketStatusEnum.OPEN.value,
                    SupportTicketStatusEnum.IN_PROGRESS.value,
                ])
            )
            .order_by(SupportTicket.created_at.desc())
            .all()
        )

    def get_latest_open(self, user_id: str) -> SupportTicket | None:
        """Get the most recent open ticket for a user."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        return (
            self.db.query(SupportTicket)
            .filter(
                SupportTicket.user_id == lookup_id,
                SupportTicket.status.in_([
                    SupportTicketStatusEnum.OPEN.value,
                    SupportTicketStatusEnum.IN_PROGRESS.value,
                ])
            )
            .order_by(SupportTicket.created_at.desc())
            .first()
        )

    def get_by_transaction_ref(self, transaction_ref: str) -> list[SupportTicket]:
        """Get tickets related to a specific transaction."""
        return (
            self.db.query(SupportTicket)
            .filter(SupportTicket.transaction_ref == transaction_ref)
            .order_by(SupportTicket.created_at.desc())
            .all()
        )

    def generate_ticket_code(self) -> str:
        """Generate a unique ticket code in format SUP-YYYYMMDD-####."""
        today = date.today()
        prefix = f"SUP-{today.strftime('%Y%m%d')}"
        
        # Get count of tickets created today
        today_count = (
            self.db.query(func.count(SupportTicket.id))
            .filter(SupportTicket.ticket_code.like(f"{prefix}-%"))
            .scalar()
        ) or 0
        
        # Increment to get next number
        next_num = today_count + 1
        
        return f"{prefix}-{next_num:04d}"
