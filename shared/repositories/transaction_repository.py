"""Repository for Transaction model."""
from uuid import UUID
from typing import List, Optional
from sqlalchemy.orm import Session

from shared.repositories.base import BaseRepository
from shared.database.models import Transaction


class TransactionRepository(BaseRepository[Transaction]):
    """Repository for Transaction operations."""

    def __init__(self, db: Session):
        super().__init__(db, Transaction)

    def get_by_user(self, user_id: str, limit: int = 20) -> List[Transaction]:
        """Get all transactions for a user, ordered by created_at descending."""
        # Convert string UUID to UUID if needed
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError:
                pass
        return (
            self.db.query(Transaction)
            .filter(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[Transaction]:
        """Get a transaction by idempotency key."""
        return (
            self.db.query(Transaction)
            .filter(Transaction.idempotency_key == idempotency_key)
            .first()
        )

    def get_by_status(self, user_id: str, status: str) -> List[Transaction]:
        """Get transactions for a user by status."""
        # Convert string UUID to UUID if needed
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError:
                pass
        return (
            self.db.query(Transaction)
            .filter(Transaction.user_id == user_id, Transaction.status == status)
            .order_by(Transaction.created_at.desc())
            .all()
        )
