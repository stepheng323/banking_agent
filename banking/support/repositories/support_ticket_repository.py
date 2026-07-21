"""Repository for SupportTicket model."""

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.enums import SupportTicketStatusEnum
from shared.database.models import SupportTicket


class SupportTicketRepository(BaseRepository[SupportTicket]):
    """Repository for SupportTicket operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, SupportTicket)

    async def get_by_ticket_code(self, ticket_code: str) -> SupportTicket | None:
        """Get a ticket by its code (e.g., SUP-20260116-0042)."""
        result = await self.db.execute(select(SupportTicket).filter(SupportTicket.ticket_code == ticket_code))
        return result.scalars().first()

    @staticmethod
    def _user_lookup(user_id: str) -> UUID | str:
        try:
            return UUID(user_id)
        except ValueError:
            return user_id

    async def get_for_user(
        self,
        user_id: str,
        *,
        ticket_id: str | None = None,
        ticket_code: str | None = None,
        for_update: bool = False,
    ) -> SupportTicket | None:
        """Resolve a ticket only inside its owner's scope."""
        query = select(SupportTicket).filter(SupportTicket.user_id == self._user_lookup(user_id))
        if ticket_id:
            try:
                lookup_ticket_id: UUID | str = UUID(ticket_id)
            except ValueError:
                lookup_ticket_id = ticket_id
            query = query.filter(SupportTicket.id == lookup_ticket_id)
        elif ticket_code:
            query = query.filter(SupportTicket.ticket_code == ticket_code)
        else:
            return None
        if for_update:
            query = query.with_for_update(nowait=True)
        result = await self.db.execute(query)
        return result.scalars().first()

    async def get_open_page(self, user_id: str, *, limit: int = 6, offset: int = 0) -> list[SupportTicket]:
        result = await self.db.execute(
            select(SupportTicket)
            .filter(
                SupportTicket.user_id == self._user_lookup(user_id),
                SupportTicket.status.in_(
                    [SupportTicketStatusEnum.OPEN.value, SupportTicketStatusEnum.IN_PROGRESS.value]
                ),
            )
            .order_by(SupportTicket.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_open(self, user_id: str) -> int:
        result = await self.db.execute(
            select(func.count(SupportTicket.id)).filter(
                SupportTicket.user_id == self._user_lookup(user_id),
                SupportTicket.status.in_(
                    [SupportTicketStatusEnum.OPEN.value, SupportTicketStatusEnum.IN_PROGRESS.value]
                ),
            )
        )
        return int(result.scalar_one())

    async def get_by_user(self, user_id: str, limit: int = 20, include_closed: bool = False) -> list[SupportTicket]:
        """Get all tickets for a user, ordered by created_at descending."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        query = select(SupportTicket).filter(SupportTicket.user_id == lookup_id)

        if not include_closed:
            query = query.filter(SupportTicket.status != SupportTicketStatusEnum.CLOSED.value)

        result = await self.db.execute(query.order_by(SupportTicket.created_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def get_open_tickets(self, user_id: str) -> list[SupportTicket]:
        """Get open tickets for a user."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(SupportTicket)
            .filter(
                SupportTicket.user_id == lookup_id,
                SupportTicket.status.in_(
                    [
                        SupportTicketStatusEnum.OPEN.value,
                        SupportTicketStatusEnum.IN_PROGRESS.value,
                    ]
                ),
            )
            .order_by(SupportTicket.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_open(self, user_id: str) -> SupportTicket | None:
        """Get the most recent open ticket for a user."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(SupportTicket)
            .filter(
                SupportTicket.user_id == lookup_id,
                SupportTicket.status.in_(
                    [
                        SupportTicketStatusEnum.OPEN.value,
                        SupportTicketStatusEnum.IN_PROGRESS.value,
                    ]
                ),
            )
            .order_by(SupportTicket.created_at.desc())
        )
        return result.scalars().first()

    async def get_by_transaction_ref(self, transaction_ref: str) -> list[SupportTicket]:
        """Get tickets related to a specific transaction."""
        result = await self.db.execute(
            select(SupportTicket)
            .filter(SupportTicket.transaction_ref == transaction_ref)
            .order_by(SupportTicket.created_at.desc())
        )
        return list(result.scalars().all())

    async def generate_ticket_code(self) -> str:
        """Generate a unique ticket code in format SUP-YYYYMMDD-####."""
        today = date.today()
        prefix = f"SUP-{today.strftime('%Y%m%d')}"

        # Get count of tickets created today
        result = await self.db.execute(
            select(func.count(SupportTicket.id)).filter(SupportTicket.ticket_code.like(f"{prefix}-%"))
        )
        today_count = result.scalar() or 0

        # Increment to get next number
        next_num = today_count + 1

        return f"{prefix}-{next_num:04d}"
