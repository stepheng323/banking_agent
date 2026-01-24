"""Repository for ActionableMessage operations."""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import ActionableMessage
from shared.repositories.base import BaseRepository


class ActionableMessageRepository(BaseRepository[ActionableMessage]):
    """Repository for ActionableMessage operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ActionableMessage)

    async def get_by_user(self, user_id: str) -> list[ActionableMessage]:
        """Get all non-expired actionable messages for a user."""
        result = await self.db.execute(
            select(ActionableMessage).filter(
                ActionableMessage.user_id == user_id,
                ActionableMessage.expires_at > datetime.utcnow(),
            )
        )
        return list(result.scalars().all())

    async def get_by_wa_message_id_for_user(self, wa_message_id: str, user_id: str) -> ActionableMessage | None:
        """Get actionable message only if owned by user.

        Security: Prevents cross-user quote hydration where User A
        could potentially hydrate from User B's receipts.
        """
        result = await self.db.execute(
            select(ActionableMessage).filter(
                ActionableMessage.wa_message_id == wa_message_id,
                ActionableMessage.user_id == user_id,
                ActionableMessage.expires_at > datetime.utcnow(),
            )
        )
        return result.scalars().first()

    async def cleanup_expired(self) -> int:
        """Delete expired messages. Returns count deleted."""
        # Note: delete() with execution.
        result = await self.db.execute(
            delete(ActionableMessage).where(ActionableMessage.expires_at <= datetime.utcnow())
        )
        await self.db.commit()
        return result.rowcount
