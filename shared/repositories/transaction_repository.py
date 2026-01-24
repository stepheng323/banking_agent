"""Repository for Transaction model."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import Transaction
from shared.repositories.base import BaseRepository


class TransactionRepository(BaseRepository[Transaction]):
    """Repository for Transaction operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, Transaction)

    async def get_by_user(self, user_id: str, limit: int = 20) -> list[Transaction]:
        """Get all transactions for a user, ordered by created_at descending."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(Transaction)
            .filter(Transaction.user_id == lookup_id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_idempotency_key(self, idempotency_key: str) -> Transaction | None:
        """Get a transaction by idempotency key."""
        result = await self.db.execute(select(Transaction).filter(Transaction.idempotency_key == idempotency_key))
        return result.scalars().first()

    async def get_by_status(self, user_id: str, status: str) -> list[Transaction]:
        """Get transactions for a user by status."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(Transaction)
            .filter(Transaction.user_id == lookup_id, Transaction.status == status)
            .order_by(Transaction.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, transaction_id: str) -> Transaction | None:
        """Get a transaction by its UUID."""
        try:
            tx_uuid = UUID(transaction_id) if isinstance(transaction_id, str) else transaction_id
            result = await self.db.execute(select(Transaction).filter(Transaction.id == tx_uuid))
            return result.scalars().first()
        except ValueError:
            return None

    async def get_by_transaction_id(self, transaction_id: str) -> Transaction | None:
        """Get a transaction by provider transaction_id."""
        result = await self.db.execute(select(Transaction).filter(Transaction.transaction_id == transaction_id))
        return result.scalars().first()

    async def get_recent_unresolved(self, user_id: str, limit: int = 5) -> list[Transaction]:
        """Get recent pending or failed transactions for a user."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        result = await self.db.execute(
            select(Transaction)
            .filter(
                Transaction.user_id == lookup_id,
                Transaction.status.in_(["pending", "failed"]),
            )
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(
        self, transaction_id: str, status: str, error_message: str | None = None
    ) -> Transaction | None:
        """Update transaction status."""
        transaction = await self.get_by_id(transaction_id)
        if transaction:
            transaction.status = status
            if error_message:
                transaction.error_message = error_message
            self.db.add(transaction)
            await self.db.commit()
            await self.db.refresh(transaction)
        return transaction
