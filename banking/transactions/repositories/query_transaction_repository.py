"""Persistence for the canonical query-side transaction read model."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.models import (
    BankTransaction,
    QueryTransaction,
    QueryTransactionSource,
    Transaction,
    TransactionSemanticProjection,
)


def normalize_narration(value: str | None) -> str:
    """Return a stable narration form suitable for matching, never presentation."""
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def narration_fingerprint(value: str | None) -> str | None:
    normalized = normalize_narration(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _app_effective_at(row: Transaction) -> datetime:
    """Use the completed time when present, otherwise the durable app timestamp."""
    value = row.completed_at or row.updated_at or row.created_at
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _app_status(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"successful", "success", "completed", "complete"}:
        return "successful"
    if normalized in {"failed", "failure", "declined", "rejected"}:
        return "failed"
    if normalized in {"reversed", "refunded"}:
        return "reversed"
    if normalized in {"pending", "processing", "queued"}:
        return normalized
    return "pending"


def _app_narration(row: Transaction) -> str | None:
    """Prefer an explicit narration, then the safe destination display name."""
    return row.narration or row.recipient_name or row.biller_item_name or row.transaction_type


class QueryTransactionRepository(BaseRepository[QueryTransaction]):
    """Build and fetch canonical query-side rows from durable source observations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, QueryTransaction)

    @staticmethod
    def _uuid(value: UUID | str | None) -> UUID | str | None:
        if value is None or isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except ValueError:
            return value

    async def project_bank_transaction(self, row: BankTransaction) -> QueryTransaction:
        """Idempotently materialize one raw bank-feed row into the query read model."""
        source_id = str(row.id)
        source = (
            (
                await self.db.execute(
                    select(QueryTransactionSource).where(
                        QueryTransactionSource.source_kind == "bank_transaction",
                        QueryTransactionSource.source_id == source_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        direction = str(row.transaction_type or "debit").lower()
        if direction not in {"debit", "credit"}:
            direction = "debit"
        # Canonical read model stores unsigned amounts; sign lives in direction.
        amount = abs(Decimal(str(row.amount)))
        query_transaction: QueryTransaction
        if source is None:
            matched_transaction = await self._matching_app_projection(
                user_id=row.user_id,
                provider_reference=row.provider_transaction_id,
            )
            if matched_transaction is None:
                query_transaction = QueryTransaction(
                    user_id=row.user_id,
                    linked_account_id=row.linked_account_id,
                    effective_at=row.posted_at,
                    effective_date=row.posted_date,
                    amount=amount,
                    currency=row.currency,
                    direction=direction,
                    status="posted",
                    narration=row.narration,
                    narration_fingerprint=narration_fingerprint(row.narration),
                    bank_name=row.bank_name,
                    source_reconciliation_status="unmatched",
                )
                self.db.add(query_transaction)
                await self.db.flush()
            else:
                query_transaction = matched_transaction
                query_transaction.source_reconciliation_status = "exact"
                query_transaction.linked_account_id = row.linked_account_id
                query_transaction.effective_at = row.posted_at
                query_transaction.effective_date = row.posted_date
                query_transaction.amount = amount
                query_transaction.currency = row.currency
                query_transaction.direction = direction
                query_transaction.status = "posted"
                query_transaction.narration = row.narration
                query_transaction.narration_fingerprint = narration_fingerprint(row.narration)
                query_transaction.bank_name = row.bank_name
            source = QueryTransactionSource(
                query_transaction_id=query_transaction.id,
                source_kind="bank_transaction",
                source_id=source_id,
                provider=row.provider,
                provider_reference=row.provider_transaction_id,
                match_confidence="exact",
                observed_payload={"provider": row.provider},
            )
            self.db.add(source)
        else:
            loaded_transaction = await self.db.get(QueryTransaction, source.query_transaction_id)
            assert loaded_transaction is not None
            query_transaction = loaded_transaction
            query_transaction.linked_account_id = row.linked_account_id
            query_transaction.effective_at = row.posted_at
            query_transaction.effective_date = row.posted_date
            query_transaction.amount = amount
            query_transaction.currency = row.currency
            query_transaction.direction = direction
            query_transaction.narration = row.narration
            query_transaction.narration_fingerprint = narration_fingerprint(row.narration)
            query_transaction.bank_name = row.bank_name
            source.provider = row.provider
            source.provider_reference = row.provider_transaction_id
        await self.db.flush()
        return query_transaction

    async def project_app_transaction(self, row: Transaction) -> QueryTransaction:
        """Project an app-initiated payment without treating it as a bank feed.

        A provider reference can later attach this source to the already-ingested
        bank observation.  Until then it remains a real canonical source in its
        own right, rather than disappearing from semantic insights.
        """
        source_id = str(row.id)
        source = (
            (
                await self.db.execute(
                    select(QueryTransactionSource).where(
                        QueryTransactionSource.source_kind == "app_transaction",
                        QueryTransactionSource.source_id == source_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        provider_reference = str(row.transaction_id or "").strip() or None
        query_transaction: QueryTransaction
        if source is None:
            matched_transaction = await self._matching_bank_projection(
                user_id=row.user_id,
                provider_reference=provider_reference,
            )
            if matched_transaction is None:
                query_transaction = QueryTransaction(
                    user_id=row.user_id,
                    linked_account_id=row.source_account_id,
                    effective_at=_app_effective_at(row),
                    effective_date=_app_effective_at(row).date(),
                    amount=Decimal(str(row.amount)),
                    currency=row.currency,
                    direction="debit",
                    status=_app_status(row.status),
                    narration=_app_narration(row),
                    narration_fingerprint=narration_fingerprint(_app_narration(row)),
                    bank_name=row.source_bank_name,
                    source_reconciliation_status="unmatched",
                )
                self.db.add(query_transaction)
                await self.db.flush()
            else:
                query_transaction = matched_transaction
                query_transaction.source_reconciliation_status = "exact"
            source = QueryTransactionSource(
                query_transaction_id=query_transaction.id,
                source_kind="app_transaction",
                source_id=source_id,
                provider="nenya",
                provider_reference=provider_reference,
                match_confidence="exact" if query_transaction.source_reconciliation_status == "exact" else "none",
                observed_payload={"transaction_type": row.transaction_type},
            )
            self.db.add(source)
        else:
            loaded_transaction = await self.db.get(QueryTransaction, source.query_transaction_id)
            assert loaded_transaction is not None
            query_transaction = loaded_transaction
            # Bank observations remain authoritative for observed timing and
            # amount when an exact source match already exists.
            if query_transaction.source_reconciliation_status != "exact":
                query_transaction.linked_account_id = row.source_account_id
                query_transaction.effective_at = _app_effective_at(row)
                query_transaction.effective_date = query_transaction.effective_at.date()
                query_transaction.amount = Decimal(str(row.amount))
                query_transaction.currency = row.currency
                query_transaction.narration = _app_narration(row)
                query_transaction.narration_fingerprint = narration_fingerprint(query_transaction.narration)
                query_transaction.bank_name = row.source_bank_name
            if query_transaction.source_reconciliation_status != "exact":
                query_transaction.status = _app_status(row.status)
            source.provider_reference = provider_reference
        await self.db.flush()
        return query_transaction

    async def _matching_bank_projection(
        self,
        *,
        user_id: UUID,
        provider_reference: str | None,
    ) -> QueryTransaction | None:
        """Find only a reference-exact bank source; never merge on amount/name."""
        if not provider_reference:
            return None
        result = await self.db.execute(
            select(QueryTransaction)
            .join(QueryTransactionSource)
            .where(
                QueryTransaction.user_id == user_id,
                QueryTransactionSource.source_kind == "bank_transaction",
                QueryTransactionSource.provider_reference == provider_reference,
            )
            .limit(1)
        )
        return result.scalars().first()

    async def _matching_app_projection(
        self,
        *,
        user_id: UUID,
        provider_reference: str | None,
    ) -> QueryTransaction | None:
        """Return only an app source with the same provider reference."""
        if not provider_reference:
            return None
        result = await self.db.execute(
            select(QueryTransaction)
            .join(QueryTransactionSource)
            .where(
                QueryTransaction.user_id == user_id,
                QueryTransactionSource.source_kind == "app_transaction",
                QueryTransactionSource.provider_reference == provider_reference,
            )
            .limit(1)
        )
        return result.scalars().first()

    async def list_by_user_window(
        self,
        user_id: UUID | str,
        *,
        start_date: date,
        end_date: date,
        limit: int = 200,
    ) -> list[QueryTransaction]:
        result = await self.db.execute(
            select(QueryTransaction)
            .where(
                QueryTransaction.user_id == self._uuid(user_id),
                QueryTransaction.effective_date >= start_date,
                QueryTransaction.effective_date <= end_date,
            )
            .order_by(QueryTransaction.effective_at.desc(), QueryTransaction.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def query_dicts_by_user_window(
        self,
        user_id: UUID | str,
        *,
        start_date: date,
        end_date: date,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return query-worker-compatible rows with the active semantic projection."""
        result = await self.db.execute(
            select(QueryTransaction, TransactionSemanticProjection)
            .outerjoin(
                TransactionSemanticProjection,
                TransactionSemanticProjection.query_transaction_id == QueryTransaction.id,
            )
            .where(
                QueryTransaction.user_id == self._uuid(user_id),
                QueryTransaction.effective_date >= start_date,
                QueryTransaction.effective_date <= end_date,
            )
            .order_by(QueryTransaction.effective_at.desc(), QueryTransaction.id.desc())
            .limit(limit)
        )
        return self._query_dicts(result.all())

    async def query_dicts_by_accounts_window(
        self,
        linked_account_ids: list[UUID | str],
        *,
        start_date: date,
        end_date: date,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return canonical query rows for a bounded linked-account scope."""
        if not linked_account_ids:
            return []
        lookup_ids = [self._uuid(value) for value in linked_account_ids]
        result = await self.db.execute(
            select(QueryTransaction, TransactionSemanticProjection)
            .outerjoin(
                TransactionSemanticProjection,
                TransactionSemanticProjection.query_transaction_id == QueryTransaction.id,
            )
            .where(
                QueryTransaction.linked_account_id.in_(lookup_ids),
                QueryTransaction.effective_date >= start_date,
                QueryTransaction.effective_date <= end_date,
            )
            .order_by(QueryTransaction.effective_at.desc(), QueryTransaction.id.desc())
            .limit(limit)
        )
        return self._query_dicts(result.all())

    async def mark_source_reconciliation(self, query_transaction_id: UUID, *, status: str) -> None:
        row = await self.db.get(QueryTransaction, query_transaction_id)
        if row is not None:
            row.source_reconciliation_status = status
            await self.db.flush()

    @staticmethod
    def _query_dicts(rows: Any) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for transaction, projection in rows:
            output.append(
                {
                    "id": str(transaction.id),
                    "transaction_id": str(transaction.id),
                    "type": transaction.direction,
                    "transaction_type": transaction.direction,
                    "amount": float(transaction.amount),
                    "currency": transaction.currency,
                    "narration": transaction.narration or "",
                    "date": transaction.effective_at.isoformat(),
                    "status": transaction.status,
                    "display_status": transaction.status,
                    "counterparty": projection.counterparty_name if projection else None,
                    "counterparty_entity_id": (
                        str(projection.counterparty_entity_id)
                        if projection and projection.counterparty_entity_id
                        else None
                    ),
                    "entity_type": projection.entity_type if projection else None,
                    "event_type": projection.event_type if projection else None,
                    "category": projection.category if projection else None,
                    "resolved_category": projection.category if projection else None,
                    "cash_flow_class": projection.cash_flow_class if projection else None,
                    "semantic_resolution_state": projection.resolution_state if projection else "unknown",
                    "bank_name": transaction.bank_name or "",
                    "source_account_id": str(transaction.linked_account_id) if transaction.linked_account_id else None,
                    "source_account_label": transaction.bank_name or "",
                    "unified_source": "canonical",
                    "source": "canonical",
                }
            )
        return output
