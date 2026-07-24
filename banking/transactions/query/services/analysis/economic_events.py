"""Deterministic economic-event projection and semantic reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.transactions.query.services.analysis.semantic_enrichment import ENRICHMENT_VERSION
from shared.database.models import (
    CounterpartyEntity,
    EconomicEvent,
    EconomicEventTransaction,
    QueryTransaction,
    SemanticReconciliationRun,
    TransactionRelationship,
    TransactionSemanticProjection,
)


@dataclass(frozen=True)
class SemanticReconciliationReport:
    status: str
    scanned_count: int
    assigned_count: int
    unresolved_count: int
    unresolved_amount: Decimal


class EconomicEventBuilder:
    """Materialize a safe one-to-one event before later relationship consolidation."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def rebuild_for_transaction(
        self,
        transaction: QueryTransaction,
        projection: TransactionSemanticProjection,
    ) -> EconomicEvent:
        """Create/update a deterministic primary event without deleting ledger facts."""
        result = await self.db.execute(
            select(EconomicEvent)
            .join(EconomicEventTransaction, EconomicEventTransaction.economic_event_id == EconomicEvent.id)
            .where(
                EconomicEventTransaction.query_transaction_id == transaction.id,
                EconomicEventTransaction.role == "primary",
            )
            .limit(1)
        )
        event = result.scalars().first()
        confidence = Decimal("0.9500") if projection.resolution_state == "resolved" else Decimal("0.7500")
        if event is None:
            event = EconomicEvent(
                user_id=transaction.user_id,
                event_type=projection.event_type or "unknown",
                economic_amount=transaction.amount,
                currency=transaction.currency,
                direction=transaction.direction,
                counterparty_entity_id=projection.counterparty_entity_id,
                category=projection.category,
                cash_flow_class=projection.cash_flow_class,
                status="final" if projection.resolution_state == "resolved" else "provisional",
                confidence=confidence,
                effective_at=transaction.effective_at,
                enrichment_version=ENRICHMENT_VERSION,
            )
            self.db.add(event)
            await self.db.flush()
            self.db.add(
                EconomicEventTransaction(
                    economic_event_id=event.id,
                    query_transaction_id=transaction.id,
                    role="primary",
                    allocated_amount=transaction.amount,
                    direction=transaction.direction,
                    confidence=confidence,
                )
            )
        else:
            event.event_type = projection.event_type or "unknown"
            event.economic_amount = transaction.amount
            event.currency = transaction.currency
            event.direction = transaction.direction
            event.counterparty_entity_id = projection.counterparty_entity_id
            event.category = projection.category
            event.cash_flow_class = projection.cash_flow_class
            event.status = "final" if projection.resolution_state == "resolved" else "provisional"
            event.confidence = confidence
            event.effective_at = transaction.effective_at
            event.enrichment_version = ENRICHMENT_VERSION
        await self.db.flush()
        return event

    async def remove_for_transaction(self, transaction_id: Any) -> None:
        """Remove a provisional event when its app source did not settle.

        Source observations remain durable in ``QueryTransaction``.  This only
        prevents pending, failed, or reversed app payments from contributing to
        economic totals before an authoritative bank observation settles them.
        """
        event_ids = (
            (
                await self.db.execute(
                    select(EconomicEventTransaction.economic_event_id).where(
                        EconomicEventTransaction.query_transaction_id == transaction_id,
                        EconomicEventTransaction.role == "primary",
                    )
                )
            )
            .scalars()
            .all()
        )
        if event_ids:
            await self.db.execute(delete(EconomicEvent).where(EconomicEvent.id.in_(event_ids)))
        await self.db.flush()

    async def link_high_confidence_candidates(self, transaction: QueryTransaction) -> int:
        """Persist non-authoritative relationship candidates for later deterministic review.

        Equal/opposite movements alone are never used to change totals.  They are
        stored as candidates so a later reference-aware linker can promote them.
        """
        candidates = (
            (
                await self.db.execute(
                    select(QueryTransaction).where(
                        QueryTransaction.user_id == transaction.user_id,
                        QueryTransaction.id != transaction.id,
                        QueryTransaction.amount == transaction.amount,
                        QueryTransaction.direction != transaction.direction,
                        QueryTransaction.effective_date >= transaction.effective_date,
                    )
                )
            )
            .scalars()
            .all()
        )
        created = 0
        for candidate in candidates:
            if abs((candidate.effective_at - transaction.effective_at).total_seconds()) > 7 * 24 * 3600:
                continue
            relation_type = "refund_of" if transaction.direction == "credit" else "reversal_of"
            exists = (
                await self.db.execute(
                    select(TransactionRelationship.id).where(
                        TransactionRelationship.source_transaction_id == transaction.id,
                        TransactionRelationship.target_transaction_id == candidate.id,
                        TransactionRelationship.relationship_type == relation_type,
                        TransactionRelationship.superseded_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue
            self.db.add(
                TransactionRelationship(
                    source_transaction_id=transaction.id,
                    target_transaction_id=candidate.id,
                    relationship_type=relation_type,
                    status="candidate",
                    confidence=Decimal("0.5000"),
                    evidence={"kind": "equal_amount_opposite_direction"},
                    enrichment_version=ENRICHMENT_VERSION,
                )
            )
            created += 1
        await self.db.flush()
        return created


class SemanticReconciler:
    """Check event allocation coverage without confusing it with operational ledger reconciliation."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def reconcile_user(self, user_id: Any) -> SemanticReconciliationReport:
        transactions = (
            (await self.db.execute(select(QueryTransaction).where(QueryTransaction.user_id == user_id))).scalars().all()
        )
        assigned_rows = await self.db.execute(
            select(EconomicEventTransaction.query_transaction_id, func.sum(EconomicEventTransaction.allocated_amount))
            .join(EconomicEvent, EconomicEvent.id == EconomicEventTransaction.economic_event_id)
            .where(EconomicEvent.user_id == user_id)
            .group_by(EconomicEventTransaction.query_transaction_id)
        )
        assigned = {row[0]: Decimal(str(row[1])) for row in assigned_rows.all()}
        unresolved = [
            transaction
            for transaction in transactions
            if transaction.id not in assigned or assigned[transaction.id] != Decimal(str(transaction.amount))
        ]
        report = SemanticReconciliationReport(
            status="confirmed" if not unresolved else "partial",
            scanned_count=len(transactions),
            assigned_count=len(transactions) - len(unresolved),
            unresolved_count=len(unresolved),
            unresolved_amount=sum((Decimal(str(item.amount)) for item in unresolved), Decimal("0")),
        )
        run = SemanticReconciliationRun(
            user_id=user_id,
            status=report.status,
            enrichment_version=ENRICHMENT_VERSION,
            scanned_count=report.scanned_count,
            assigned_count=report.assigned_count,
            unresolved_count=report.unresolved_count,
            unresolved_amount=report.unresolved_amount,
            details={"kind": "event_allocation"},
            started_at=datetime.now(UTC).replace(tzinfo=None),
            finished_at=datetime.now(UTC).replace(tzinfo=None),
        )
        self.db.add(run)
        await self.db.flush()
        return report


async def load_economic_event_rows(
    db: AsyncSession,
    *,
    user_id: Any,
    start_date: date,
    end_date: date,
    linked_account_ids: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Return event-projection rows in the existing query handler shape."""
    statement = (
        select(EconomicEvent, CounterpartyEntity, QueryTransaction)
        .join(EconomicEventTransaction, EconomicEventTransaction.economic_event_id == EconomicEvent.id)
        .join(QueryTransaction, QueryTransaction.id == EconomicEventTransaction.query_transaction_id)
        .outerjoin(CounterpartyEntity, CounterpartyEntity.id == EconomicEvent.counterparty_entity_id)
        .where(
            EconomicEvent.user_id == user_id,
            EconomicEvent.effective_at >= datetime.combine(start_date, datetime.min.time()),
            EconomicEvent.effective_at < datetime.combine(end_date, datetime.max.time()),
        )
        .order_by(EconomicEvent.effective_at.desc(), EconomicEvent.id.desc())
    )
    if linked_account_ids:
        statement = statement.where(QueryTransaction.linked_account_id.in_(linked_account_ids))
    result = await db.execute(statement)
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for event, entity, transaction in result.all():
        if event.id in seen:
            continue
        seen.add(event.id)
        rows.append(
            {
                "id": str(event.id),
                "transaction_id": str(event.id),
                "type": event.direction,
                "transaction_type": event.event_type,
                "amount": float(event.economic_amount),
                "currency": event.currency,
                "narration": event.event_type.replace("_", " ").title(),
                "date": event.effective_at.isoformat(),
                "status": "posted",
                "display_status": "posted",
                "counterparty": entity.canonical_name if entity is not None else None,
                "counterparty_entity_id": str(event.counterparty_entity_id) if event.counterparty_entity_id else None,
                "entity_type": entity.entity_type if entity is not None else None,
                "event_type": event.event_type,
                "category": event.category,
                "resolved_category": event.category,
                "cash_flow_class": event.cash_flow_class,
                "semantic_resolution_state": "resolved" if event.status == "final" else "partial",
                "bank_name": transaction.bank_name or "",
                "source_account_id": str(transaction.linked_account_id) if transaction.linked_account_id else None,
                "source_account_label": transaction.bank_name or "",
                "unified_source": "economic_event",
                "source": "economic_event",
                "is_internal_transfer": event.cash_flow_class == "internal",
            }
        )
    return rows
