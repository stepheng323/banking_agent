"""Repository for mirrored bank-feed transactions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import BankTransaction
from banking.persistence.base import BaseRepository


def normalize_db_timestamp(value: datetime) -> datetime:
    """Normalize timestamps for naive Postgres TIMESTAMP columns."""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


class BankTransactionRepository(BaseRepository[BankTransaction]):
    """Repository for mirrored bank transactions."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, BankTransaction)

    @staticmethod
    def _coerce_uuid(value: str | UUID) -> str | UUID:
        if isinstance(value, UUID):
            return value
        try:
            return UUID(value)
        except ValueError:
            return value

    async def bulk_upsert(self, rows: list[dict]) -> int:
        """Insert or update mirrored transactions by provider transaction id."""
        if not rows:
            return 0

        now = normalize_db_timestamp(datetime.now(UTC))
        payloads = []
        for row in rows:
            payload = dict(row)
            payload["linked_account_id"] = self._coerce_uuid(payload["linked_account_id"])
            payload["user_id"] = self._coerce_uuid(payload["user_id"])
            payload.setdefault("first_seen_at", now)
            payload["last_seen_at"] = now
            payloads.append(payload)

        stmt = pg_insert(BankTransaction).values(payloads)
        stmt = stmt.on_conflict_do_update(
            index_elements=["linked_account_id", "provider", "provider_transaction_id"],
            set_={
                "posted_at": stmt.excluded.posted_at,
                "posted_date": stmt.excluded.posted_date,
                "amount": stmt.excluded.amount,
                "currency": stmt.excluded.currency,
                "transaction_type": stmt.excluded.transaction_type,
                "narration": stmt.excluded.narration,
                "category": stmt.excluded.category,
                "counterparty": stmt.excluded.counterparty,
                "counterparty_role": stmt.excluded.counterparty_role,
                "counterparty_source": stmt.excluded.counterparty_source,
                "resolved_category": stmt.excluded.resolved_category,
                "category_source": stmt.excluded.category_source,
                "parser_rule": stmt.excluded.parser_rule,
                "bank_name": stmt.excluded.bank_name,
                "raw_payload": stmt.excluded.raw_payload,
                "last_seen_at": now,
            },
        )
        await self.db.execute(stmt)
        await self.db.flush()
        return len(payloads)

    async def list_by_account_window(
        self,
        linked_account_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ) -> list[BankTransaction]:
        """Return transactions for an account within a date window."""
        lookup_id = self._coerce_uuid(linked_account_id)
        result = await self.db.execute(
            select(BankTransaction)
            .filter(
                BankTransaction.linked_account_id == lookup_id,
                BankTransaction.provider == provider,
                BankTransaction.posted_date >= start_date,
                BankTransaction.posted_date <= end_date,
            )
            .order_by(BankTransaction.posted_at.desc(), BankTransaction.provider_transaction_id.desc())
        )
        return list(result.scalars().all())

    async def list_by_accounts_window(
        self,
        linked_account_ids: list[str | UUID],
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
    ) -> list[BankTransaction]:
        """Return transactions for multiple accounts within a date window."""
        if not linked_account_ids:
            return []

        lookup_ids = [self._coerce_uuid(account_id) for account_id in linked_account_ids]
        result = await self.db.execute(
            select(BankTransaction)
            .filter(
                BankTransaction.linked_account_id.in_(lookup_ids),
                BankTransaction.provider == provider,
                BankTransaction.posted_date >= start_date,
                BankTransaction.posted_date <= end_date,
            )
            .order_by(BankTransaction.posted_at.desc(), BankTransaction.provider_transaction_id.desc())
        )
        return list(result.scalars().all())

    async def list_by_user_window(
        self,
        user_id: str | UUID,
        *,
        start_date: date,
        end_date: date,
        provider: str = "mono",
        limit: int = 200,
    ) -> list[BankTransaction]:
        """Return mirrored bank transactions for a user within a date window."""
        lookup_id = self._coerce_uuid(user_id)
        result = await self.db.execute(
            select(BankTransaction)
            .filter(
                BankTransaction.user_id == lookup_id,
                BankTransaction.provider == provider,
                BankTransaction.posted_date >= start_date,
                BankTransaction.posted_date <= end_date,
            )
            .order_by(BankTransaction.posted_at.desc(), BankTransaction.provider_transaction_id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_latest_posted_at(
        self,
        linked_account_id: str | UUID,
        *,
        provider: str = "mono",
    ) -> datetime | None:
        """Return the latest mirrored transaction timestamp for an account."""
        lookup_id = self._coerce_uuid(linked_account_id)
        result = await self.db.execute(
            select(BankTransaction)
            .filter(
                BankTransaction.linked_account_id == lookup_id,
                BankTransaction.provider == provider,
            )
            .order_by(BankTransaction.posted_at.desc())
            .limit(1)
        )
        transaction = result.scalars().first()
        return transaction.posted_at if transaction is not None else None
