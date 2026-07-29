"""Shared deterministic insight filtering and presentation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from banking.transactions.query.models.domain import QueryResultItem
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    CoverageStatus,
    InsightResultMetadata,
)
from banking.transactions.query.services.analysis.kernel.metrics import (
    row_amount,
    row_effective_datetime,
    row_is_fee,
    row_is_internal,
    row_is_operating,
    row_is_settled,
    row_is_uncertain,
)


def inclusive_window(*, end: date, days: int) -> tuple[date, date]:
    """Return an inclusive N-day window ending on ``end``."""
    bounded_days = max(days, 1)
    return end - timedelta(days=bounded_days - 1), end


def filter_insight_rows(
    dataset: AnalysisDataset,
    *,
    confidence_policy: Literal["include", "exclude_uncertain", "segment_uncertain"],
    include_fees: bool = False,
    include_non_operating: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows into included, excluded, and uncertain segments."""
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    for row in dataset.rows:
        if not row_is_settled(row) or row_is_internal(row):
            excluded.append(row)
            continue
        if not include_fees and row_is_fee(row):
            excluded.append(row)
            continue
        if not include_non_operating and not row_is_operating(row):
            excluded.append(row)
            continue
        if row_is_uncertain(row):
            uncertain.append(row)
            if confidence_policy == "include":
                included.append(row)
            elif confidence_policy == "exclude_uncertain":
                excluded.append(row)
            continue
        included.append(row)
    return included, excluded, uncertain


def insight_available(
    dataset: AnalysisDataset,
    *,
    completeness_policy: Literal["disclose", "require_complete"],
) -> tuple[bool, str | None]:
    """Apply shared completeness policy to an insight dataset."""
    if dataset.coverage_status == CoverageStatus.UNAVAILABLE:
        return False, "coverage_unavailable"
    if completeness_policy == "require_complete" and dataset.coverage_status != CoverageStatus.COMPLETE:
        return False, "coverage_incomplete"
    return True, None


def build_insight_metadata(
    dataset: AnalysisDataset,
    *,
    included: Iterable[dict[str, Any]] = (),
    excluded: Iterable[dict[str, Any]] = (),
    uncertain: Iterable[dict[str, Any]] = (),
    available: bool = True,
    unavailable_reason: str | None = None,
) -> InsightResultMetadata:
    """Build shared privacy-safe analytical accounting metadata."""
    included_rows = list(included)
    excluded_rows = list(excluded)
    uncertain_rows = list(uncertain)
    return InsightResultMetadata(
        basis=dataset.basis,
        effective_start=dataset.start_date,
        effective_end=dataset.end_date,
        coverage=dataset.coverage_status,
        included_count=len(included_rows),
        included_value=sum((row_amount(row) for row in included_rows), Decimal("0")),
        excluded_count=len(excluded_rows),
        excluded_value=sum((row_amount(row) for row in excluded_rows), Decimal("0")),
        uncertain_count=len(uncertain_rows),
        uncertain_value=sum((row_amount(row) for row in uncertain_rows), Decimal("0")),
        available=available,
        unavailable_reason=unavailable_reason,
    )


def evidence_item(row: dict[str, Any], *, fallback_id: str) -> dict[str, Any]:
    """Convert an authoritative transaction row into a query-result item payload."""
    effective_at = row_effective_datetime(row)
    return QueryResultItem(
        id=str(row.get("transaction_id") or row.get("id") or fallback_id),
        description=str(row.get("narration") or row.get("counterparty") or "Transaction"),
        amount=float(row_amount(row)),
        date=effective_at.date() if effective_at is not None else date.min,
        metadata=dict(row),
    ).model_dump()
