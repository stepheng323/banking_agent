"""Repository for ActionableMessage operations."""

from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session

from shared.repositories.base import BaseRepository
from shared.database.models import ActionableMessage


class ActionableMessageRepository(BaseRepository[ActionableMessage]):
    """Repository for ActionableMessage operations."""

    def __init__(self, db: Session):
        super().__init__(db, ActionableMessage)

    def get_by_user(self, user_id: str) -> List[ActionableMessage]:
        """Get all non-expired actionable messages for a user."""
        return (
            self.db.query(ActionableMessage)
            .filter(
                ActionableMessage.user_id == user_id,
                ActionableMessage.expires_at > datetime.utcnow()
            )
            .all()
        )

    def get_by_wa_message_id(self, wa_message_id: str) -> Optional[ActionableMessage]:
        """Get actionable message by WhatsApp message ID (if not expired)."""
        return (
            self.db.query(ActionableMessage)
            .filter(
                ActionableMessage.wa_message_id == wa_message_id,
                ActionableMessage.expires_at > datetime.utcnow()
            )
            .first()
        )

    def cleanup_expired(self) -> int:
        """Delete expired messages. Returns count deleted."""
        result = (
            self.db.query(ActionableMessage)
            .filter(ActionableMessage.expires_at <= datetime.utcnow())
            .delete()
        )
        self.db.commit()
        return result