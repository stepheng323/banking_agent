"""Deterministic, persisted transaction semantic enrichment.

This module is intentionally independent from the conversational query path: it
projects facts at ingestion and exposes a stable semantic read model.  A future
model-assisted worker may submit assertions through the same service, but it is
never required to answer a user turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.transactions.query.services.analysis.narration import analyze_transaction_narration
from banking.transactions.repositories.query_transaction_repository import normalize_narration
from shared.database.models import (
    CounterpartyAlias,
    CounterpartyEntity,
    QueryTransaction,
    TransactionSemanticAssertion,
    TransactionSemanticProjection,
)

ENRICHMENT_VERSION = "semantic-v1"
_SEMANTIC_FIELDS = ("counterparty", "entity_type", "event_type", "category", "cash_flow_class")


@dataclass(frozen=True)
class SemanticValues:
    counterparty_name: str | None
    entity_type: str | None
    event_type: str | None
    category: str | None
    cash_flow_class: str | None
    source: str
    confidence: Decimal
    rule_id: str | None
    resolution_state: str


def infer_semantic_values(
    *,
    narration: str | None,
    direction: str,
    provider_category: str | None = None,
    provider_counterparty: str | None = None,
) -> SemanticValues:
    """Classify known semantics without overloading category as event meaning."""
    analysis = analyze_transaction_narration(
        narration=narration or "",
        transaction_type=direction,
        provider_category=provider_category,
        provider_counterparty=provider_counterparty,
    )
    normalized = normalize_narration(narration)
    category = analysis.resolved_category or provider_category
    counterparty = analysis.counterparty or provider_counterparty
    event_type: str | None = None
    entity_type: str | None = None
    cash_flow_class: str | None = None

    if "revers" in normalized or "refund" in normalized:
        event_type = "refund" if direction == "credit" else "reversal"
        cash_flow_class = "excluded"
    elif "salary" in normalized or "payroll" in normalized:
        event_type = "salary"
        entity_type = "employer"
        cash_flow_class = "operating"
    elif any(token in normalized for token in ("loan", "easemoni", "fairmoney", "carbon")):
        entity_type = "lender"
        event_type = "loan_disbursement" if direction == "credit" else "loan_repayment"
        cash_flow_class = "financing"
    elif any(token in normalized for token in ("piggyvest", "cowrywise", "owealth", "investment")):
        entity_type = "investment_platform"
        event_type = "investment_withdrawal" if direction == "credit" else "investment_contribution"
        cash_flow_class = "investing"
    elif "internal transfer" in normalized or "own account" in normalized:
        entity_type = "self"
        event_type = "internal_transfer"
        cash_flow_class = "internal"
    elif "charge" in normalized or "fee" in normalized:
        entity_type = "bank"
        event_type = "fee"
        cash_flow_class = "operating"
    elif direction == "debit":
        event_type = "purchase"
        entity_type = "merchant" if counterparty else None
        cash_flow_class = "operating"
    elif direction == "credit":
        event_type = "transfer_in"
        cash_flow_class = "operating"

    resolved_values = [counterparty, event_type, category, cash_flow_class]
    resolution_state = "resolved" if all(resolved_values) else "partial" if any(resolved_values) else "unknown"
    source = "provider" if provider_category or provider_counterparty else "rule"
    confidence = Decimal("0.9000") if source == "provider" else Decimal("0.7500")
    return SemanticValues(
        counterparty_name=counterparty,
        entity_type=entity_type,
        event_type=event_type,
        category=category,
        cash_flow_class=cash_flow_class,
        source=source,
        confidence=confidence,
        rule_id=analysis.parser_rule,
        resolution_state=resolution_state,
    )


class SemanticEnrichmentService:
    """Resolve persistent deterministic assertions and a winning projection."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def enrich_query_transaction(
        self,
        transaction: QueryTransaction,
        *,
        provider_category: str | None = None,
        provider_counterparty: str | None = None,
    ) -> TransactionSemanticProjection:
        values = infer_semantic_values(
            narration=transaction.narration,
            direction=transaction.direction,
            provider_category=provider_category,
            provider_counterparty=provider_counterparty,
        )
        entity = await self._resolve_entity(transaction.user_id, values.counterparty_name)
        if entity is not None:
            values = SemanticValues(
                counterparty_name=entity.canonical_name,
                entity_type=entity.entity_type if entity.entity_type != "unknown" else values.entity_type,
                event_type=values.event_type,
                category=entity.default_category or values.category,
                cash_flow_class=values.cash_flow_class,
                source="registry" if values.source != "provider" else values.source,
                confidence=max(values.confidence, Decimal("0.9500")),
                rule_id=values.rule_id,
                resolution_state=values.resolution_state,
            )

        projection = await self.db.get(TransactionSemanticProjection, transaction.id)
        if projection is None:
            projection = TransactionSemanticProjection(
                query_transaction_id=transaction.id,
                enrichment_version=ENRICHMENT_VERSION,
            )
            self.db.add(projection)

        winner = await self._user_assertion_values(transaction.id)
        projected = {
            "counterparty": winner.get("counterparty", values.counterparty_name),
            "entity_type": winner.get("entity_type", values.entity_type),
            "event_type": winner.get("event_type", values.event_type),
            "category": winner.get("category", values.category),
            "cash_flow_class": winner.get("cash_flow_class", values.cash_flow_class),
        }
        projection.counterparty_entity_id = entity.id if entity is not None else None
        projection.counterparty_name = _text_value(projected["counterparty"])
        projection.entity_type = _text_value(projected["entity_type"])
        projection.event_type = _text_value(projected["event_type"])
        projection.category = _text_value(projected["category"])
        projection.cash_flow_class = _text_value(projected["cash_flow_class"])
        projection.resolution_state = "resolved" if all(projected.values()) else values.resolution_state
        projection.enrichment_version = ENRICHMENT_VERSION

        for field_name, value in projected.items():
            if value is None:
                continue
            if field_name in winner:
                continue
            await self._replace_deterministic_assertion(
                query_transaction_id=transaction.id,
                field_name=field_name,
                value=value,
                values=values,
            )
        await self.db.flush()
        return projection

    async def apply_user_correction(
        self,
        *,
        transaction: QueryTransaction,
        field_name: str,
        value: str,
    ) -> TransactionSemanticProjection:
        """Persist a reviewed user correction without mutating source facts."""
        if field_name not in _SEMANTIC_FIELDS or not value.strip():
            raise ValueError("invalid_semantic_correction")
        now = datetime.now(UTC).replace(tzinfo=None)
        active = (
            (
                await self.db.execute(
                    select(TransactionSemanticAssertion).where(
                        TransactionSemanticAssertion.query_transaction_id == transaction.id,
                        TransactionSemanticAssertion.field_name == field_name,
                        TransactionSemanticAssertion.superseded_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for assertion in active:
            assertion.superseded_at = now
        self.db.add(
            TransactionSemanticAssertion(
                query_transaction_id=transaction.id,
                field_name=field_name,
                value_json={"value": value.strip()},
                source="user",
                confidence=Decimal("1"),
                resolution_state="resolved",
                enrichment_version=ENRICHMENT_VERSION,
                evidence={"kind": "user_confirmed"},
            )
        )
        await self.db.flush()
        return await self.enrich_query_transaction(transaction)

    async def _resolve_entity(self, user_id: Any, counterparty: str | None) -> CounterpartyEntity | None:
        normalized = normalize_narration(counterparty)
        if not normalized:
            return None
        result = await self.db.execute(
            select(CounterpartyEntity)
            .join(CounterpartyAlias, CounterpartyAlias.entity_id == CounterpartyEntity.id)
            .where(
                CounterpartyAlias.alias_normalized == normalized,
                (CounterpartyEntity.owner_user_id.is_(None)) | (CounterpartyEntity.owner_user_id == user_id),
            )
            .order_by(CounterpartyEntity.owner_user_id.desc().nullslast())
            .limit(1)
        )
        return result.scalars().first()

    async def _user_assertion_values(self, transaction_id: Any) -> dict[str, str]:
        result = await self.db.execute(
            select(TransactionSemanticAssertion).where(
                TransactionSemanticAssertion.query_transaction_id == transaction_id,
                TransactionSemanticAssertion.source == "user",
                TransactionSemanticAssertion.superseded_at.is_(None),
            )
        )
        values: dict[str, str] = {}
        for assertion in result.scalars().all():
            value = _text_value(assertion.value_json.get("value"))
            if value is not None:
                values[assertion.field_name] = value
        return values

    async def _replace_deterministic_assertion(
        self,
        *,
        query_transaction_id: Any,
        field_name: str,
        value: str,
        values: SemanticValues,
    ) -> None:
        current = (
            (
                await self.db.execute(
                    select(TransactionSemanticAssertion).where(
                        TransactionSemanticAssertion.query_transaction_id == query_transaction_id,
                        TransactionSemanticAssertion.field_name == field_name,
                        TransactionSemanticAssertion.source != "user",
                        TransactionSemanticAssertion.superseded_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(current) == 1 and _text_value(current[0].value_json.get("value")) == value:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        for assertion in current:
            assertion.superseded_at = now
        self.db.add(
            TransactionSemanticAssertion(
                query_transaction_id=query_transaction_id,
                field_name=field_name,
                value_json={"value": value},
                source=values.source,
                confidence=values.confidence,
                resolution_state=values.resolution_state,
                rule_id=values.rule_id,
                enrichment_version=ENRICHMENT_VERSION,
                evidence={"kind": "deterministic"},
            )
        )


def _text_value(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
