"""Repository for ActionableMessage operations."""

from datetime import datetime

from sqlalchemy.orm import Session

from shared.database.models import ActionableMessage
from shared.repositories.base import BaseRepository


class ActionableMessageRepository(BaseRepository[ActionableMessage]):
    """Repository for ActionableMessage operations."""

    def __init__(self, db: Session):
        super().__init__(db, ActionableMessage)

    def get_by_user(self, user_id: str) -> list[ActionableMessage]:
        """Get all non-expired actionable messages for a user."""
        return (
            self.db.query(ActionableMessage)
            .filter(
                ActionableMessage.user_id == user_id,
                ActionableMessage.expires_at > datetime.utcnow(),
            )
            .all()
        )

    def get_by_wa_message_id_for_user(self, wa_message_id: str, user_id: str) -> ActionableMessage | None:
        """Get actionable message only if owned by user.

        Security: Prevents cross-user quote hydration where User A
        could potentially hydrate from User B's receipts.
        """
        return (
            self.db.query(ActionableMessage)
            .filter(
                ActionableMessage.wa_message_id == wa_message_id,
                ActionableMessage.user_id == user_id,
                ActionableMessage.expires_at > datetime.utcnow(),
            )
            .first()
        )

    def cleanup_expired(self) -> int:
        """Delete expired messages. Returns count deleted."""
        result = self.db.query(ActionableMessage).filter(ActionableMessage.expires_at <= datetime.utcnow()).delete()
        self.db.commit()
        return result
