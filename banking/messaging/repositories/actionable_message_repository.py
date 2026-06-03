"""Repository for ActionableMessage operations."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.models import ActionableMessage
from shared.utils.datetime import utc_now_naive


class ActionableMessageRepository(BaseRepository[ActionableMessage]):
    """Repository for ActionableMessage operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ActionableMessage)

    @staticmethod
    def _normalize_user_id(user_id: str) -> UUID | str:
        """Normalize user_id to UUID when possible for consistent filtering."""
        try:
            return UUID(user_id)
        except ValueError:
            return user_id

    async def get_by_user(self, user_id: str) -> list[ActionableMessage]:
        """Get all non-expired actionable messages for a user."""
        lookup_user_id = self._normalize_user_id(user_id)
        result = await self.db.execute(
            select(ActionableMessage).filter(
                ActionableMessage.user_id == lookup_user_id,
                ActionableMessage.expires_at > utc_now_naive(),
            )
        )
        return list(result.scalars().all())

    async def get_by_channel_message_id_for_user(
        self, channel_message_id: str, user_id: str
    ) -> ActionableMessage | None:
        """Get actionable message only if owned by user.

        Security: Prevents cross-user quote hydration where User A
        could potentially hydrate from User B's receipts.
        """
        lookup_user_id = self._normalize_user_id(user_id)
        result = await self.db.execute(
            select(ActionableMessage).filter(
                ActionableMessage.channel_message_id == channel_message_id,
                ActionableMessage.user_id == lookup_user_id,
                ActionableMessage.expires_at > utc_now_naive(),
            )
        )
        return result.scalars().first()

    async def cleanup_expired(self) -> int:
        """Delete expired messages. Returns count deleted."""

        result = await self.db.execute(delete(ActionableMessage).where(ActionableMessage.expires_at <= utc_now_naive()))
        await self.db.commit()

        cursor_result = cast(CursorResult[Any], result)
        return cursor_result.rowcount
