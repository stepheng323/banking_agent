"""Relationship detection candidate builders."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    AnalysisMetric,
    DuplicateCandidateGroup,
    ProbableDuplicatesResult,
)
from banking.transactions.query.services.analysis.kernel.insight_support import (
    build_insight_metadata,
    insight_available,
)
from banking.transactions.query.services.analysis.kernel.metrics import (
    row_amount,
    row_counterparty_key,
    row_currency,
    row_direction,
    row_effective_datetime,
    row_is_fee,
    row_is_internal,
    row_is_settled,
    row_is_uncertain,
    row_source_account_key,
)


def _reference(row: dict[str, Any]) -> str:
    return str(row.get("transaction_reference") or row.get("reference") or row.get("provider_reference") or "").strip()


def _transaction_id(row: dict[str, Any]) -> str:
    return str(row.get("transaction_id") or row.get("id") or "").strip()


def _event_type(row: dict[str, Any]) -> str:
    return str(row.get("event_type") or row.get("transaction_type") or "").strip().lower()


def _group_id(parts: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    transaction_ids = sorted(filter(None, (_transaction_id(row) for row in rows)))
    material = "|".join((*parts, *transaction_ids))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _candidate(
    *,
    parts: tuple[str, ...],
    rows: list[dict[str, Any]],
    confidence: float,
) -> DuplicateCandidateGroup:
    amount = row_amount(rows[0])
    direction = row_direction(rows[0])
    return DuplicateCandidateGroup(
        group_id=_group_id(parts, rows),
        confidence=confidence,
        transactions=rows,
        redundant_value=amount * (len(rows) - 1),
        metric=AnalysisMetric.INCOME if direction == "credit" else AnalysisMetric.SPENDING,
    )


def detect_probable_duplicates(
    dataset: AnalysisDataset,
    min_confidence: float = 0.8,
    *,
    completeness_policy: Literal["disclose", "require_complete"] = "disclose",
) -> ProbableDuplicatesResult:
    """Detect conservative duplicate source observations and near-identical events."""
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    for row in dataset.rows:
        if (
            not row_is_settled(row)
            or row_is_internal(row)
            or row_is_fee(row)
            or row_direction(row) not in {"credit", "debit"}
        ):
            excluded.append(row)
            continue
        if row_is_uncertain(row):
            uncertain.append(row)
            excluded.append(row)
            continue
        eligible.append(row)

    available, unavailable_reason = insight_available(
        dataset,
        completeness_policy=completeness_policy,
    )

    reference_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    proximity_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        amount = str(row_amount(row))
        currency = row_currency(row)
        direction = row_direction(row)
        account = row_source_account_key(row)
        reference = _reference(row)
        if reference:
            reference_groups[(reference, amount, currency, direction, account)].append(row)
            continue
        counterparty = row_counterparty_key(row)
        event_type = _event_type(row)
        if not counterparty or not account or not event_type or row_effective_datetime(row) is None:
            excluded.append(row)
            continue
        proximity_groups[(counterparty, account, event_type, amount, currency, direction)].append(row)

    candidates: list[DuplicateCandidateGroup] = []
    for key, rows in reference_groups.items():
        if len(rows) > 1:
            candidates.append(_candidate(parts=key, rows=rows, confidence=1.0))

    for key, rows in proximity_groups.items():
        ordered = sorted(rows, key=lambda row: row_effective_datetime(row) or dataset.start_date)
        cluster: list[dict[str, Any]] = []
        cluster_start = None
        for row in ordered:
            effective_at = row_effective_datetime(row)
            if effective_at is None:
                continue
            if cluster_start is None or effective_at - cluster_start <= timedelta(seconds=60):
                cluster.append(row)
                cluster_start = cluster_start or effective_at
                continue
            if len(cluster) > 1:
                candidates.append(_candidate(parts=key, rows=cluster, confidence=0.9))
            cluster = [row]
            cluster_start = effective_at
        if len(cluster) > 1:
            candidates.append(_candidate(parts=key, rows=cluster, confidence=0.9))

    candidates = [candidate for candidate in candidates if candidate.confidence >= min_confidence]
    candidates.sort(key=lambda candidate: candidate.redundant_value, reverse=True)
    if not available:
        candidates = []
    total_redundant = sum((candidate.redundant_value for candidate in candidates), Decimal("0"))
    metadata = build_insight_metadata(
        dataset,
        included=eligible,
        excluded=excluded,
        uncertain=uncertain,
        available=available,
        unavailable_reason=unavailable_reason,
    )
    return ProbableDuplicatesResult(
        basis=dataset.basis,
        dataset=dataset,
        candidates=candidates,
        total_redundant_value=total_redundant,
        coverage=dataset.coverage_status,
        available=available,
        unavailable_reason="coverage_incomplete" if not available else None,
        metadata=metadata,
    )
