"""Repository for Transaction model."""

from datetime import UTC, date, datetime, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.enums import TransactionTypeEnum
from shared.database.models import Transaction
from shared.repositories.base import BaseRepository


def normalize_db_timestamp(value: datetime) -> datetime:
    """Normalize timestamps for naive Postgres TIMESTAMP columns."""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


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

    async def list_by_user_window(
        self,
        user_id: str,
        *,
        start_date: date,
        end_date: date,
        limit: int = 200,
    ) -> list[Transaction]:
        """Get transactions for a user whose local lifecycle touches a date window."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        window_start = datetime.combine(start_date, time.min)
        window_end = datetime.combine(end_date, time.max)
        result = await self.db.execute(
            select(Transaction)
            .filter(
                Transaction.user_id == lookup_id,
                Transaction.created_at >= window_start,
                Transaction.created_at <= window_end,
            )
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

    async def get_successful_transfers_since(
        self,
        user_id: str,
        since: datetime,
        limit: int = 500,
    ) -> list[Transaction]:
        """Get successful transfer transactions since a timestamp."""
        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        since = normalize_db_timestamp(since)

        result = await self.db.execute(
            select(Transaction)
            .filter(
                Transaction.user_id == lookup_id,
                Transaction.transaction_type == TransactionTypeEnum.TRANSFER.value,
                Transaction.status == "successful",
                Transaction.created_at >= since,
            )
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent_successful_transfer_by_recipient(
        self,
        user_id: str,
        recipient_name: str,
    ) -> Transaction | None:
        """Get most recent successful transfer where recipient name matches loosely."""
        if not recipient_name:
            return None

        lookup_id: UUID | str = user_id
        try:
            lookup_id = UUID(user_id)
        except ValueError:
            pass

        pattern = f"%{recipient_name.strip()}%"
        result = await self.db.execute(
            select(Transaction)
            .filter(
                Transaction.user_id == lookup_id,
                Transaction.transaction_type == TransactionTypeEnum.TRANSFER.value,
                Transaction.status == "successful",
                Transaction.recipient_name.ilike(pattern),
            )
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def update_status(
        self,
        transaction_id: str,
        status: str,
        error_message: str | None = None,
        *,
        provider_transaction_id: str | None = None,
        provider_status: str | None = None,
        provider_response: dict | None = None,
        provider_error_code: str | None = None,
    ) -> Transaction | None:
        """Update transaction status and optional provider metadata."""
        transaction = await self.get_by_id(transaction_id)
        if transaction:
            transaction.status = status
            if provider_transaction_id:
                transaction.transaction_id = provider_transaction_id
            if provider_status:
                transaction.provider_status = provider_status
            if provider_response is not None:
                transaction.provider_response = provider_response
            if provider_error_code:
                transaction.provider_error_code = provider_error_code
            if error_message:
                transaction.error_message = error_message
            self.db.add(transaction)
            await self.db.commit()
            await self.db.refresh(transaction)
        return transaction
