"""Deterministic recurring-transaction insight."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any, Literal

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import (
    RecurringPatternsEvidenceSelection,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation, RecurringPatternsSpec
from banking.transactions.query.services.analysis.kernel.contracts import (
    RecurringPatternsResult,
    RecurringSeries,
)
from banking.transactions.query.services.analysis.kernel.insight_support import (
    build_insight_metadata,
    evidence_item,
    filter_insight_rows,
    inclusive_window,
    insight_available,
)
from banking.transactions.query.services.analysis.kernel.metrics import (
    row_amount,
    row_counterparty_key,
    row_counterparty_label,
    row_currency,
    row_direction,
    row_effective_datetime,
    row_source_account_key,
)
from banking.transactions.query.services.analysis.kernel.service import AnalysisService


def _transaction_id(row: dict[str, Any]) -> str:
    return str(row.get("transaction_id") or row.get("id") or "").strip()


def _effective_date(row: dict[str, Any]) -> date | None:
    value = row_effective_datetime(row)
    return value.date() if value is not None else None


def _series_frequency(intervals: list[int]) -> tuple[Literal["weekly", "monthly"] | None, float]:
    if not intervals:
        return None, 0.0
    median_interval = float(median(intervals))
    if 5 <= median_interval <= 9:
        frequency: Literal["weekly", "monthly"] = "weekly"
        lower, upper = 5, 9
    elif 25 <= median_interval <= 35:
        frequency = "monthly"
        lower, upper = 25, 35
    else:
        return None, 0.0
    compliant = sum(lower <= interval <= upper for interval in intervals)
    confidence = compliant / len(intervals)
    return (frequency, confidence) if confidence >= 0.8 else (None, confidence)


async def execute_recurring_patterns(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> RecurringPatternsResult:
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation) or not isinstance(operation.analysis, RecurringPatternsSpec):
        raise ValueError("recurring-pattern execution requires RecurringPatternsSpec")
    analysis = operation.analysis
    start, end = inclusive_window(end=operation.scope.period.end, days=analysis.lookback_days)
    expanded_contract = contract.model_copy(
        deep=True,
        update={
            "operation": operation.model_copy(
                update={
                    "scope": operation.scope.model_copy(
                        update={"period": operation.scope.period.model_copy(update={"start": start, "end": end})}
                    )
                }
            )
        },
    )
    dataset = await service.load_dataset(
        expanded_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=analysis.analysis_basis,
        period_label="current",
    )
    included, excluded, uncertain = filter_insight_rows(
        dataset,
        confidence_policy=analysis.confidence_policy,
    )
    available, unavailable_reason = insight_available(
        dataset,
        completeness_policy=analysis.completeness_policy,
    )

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in included:
        counterparty = row_counterparty_key(row)
        direction = row_direction(row)
        account = row_source_account_key(row)
        if not counterparty or direction not in {"credit", "debit"} or not account:
            excluded.append(row)
            continue
        groups[(counterparty, direction, row_currency(row), account)].append(row)

    series_list: list[RecurringSeries] = []
    for group_key, rows in groups.items():
        dated_rows = [(row, _effective_date(row)) for row in rows]
        ordered = [pair for pair in dated_rows if pair[1] is not None]
        ordered.sort(key=lambda pair: pair[1] or date.min)
        if len(ordered) < 3:
            continue
        dates = [pair[1] for pair in ordered if pair[1] is not None]
        intervals = [(dates[index] - dates[index - 1]).days for index in range(1, len(dates))]
        frequency, confidence = _series_frequency(intervals)
        if frequency is None:
            continue
        transaction_rows = [pair[0] for pair in ordered]
        amounts = [row_amount(row) for row in transaction_rows]
        median_amount = Decimal(str(median(amounts)))
        label = row_counterparty_label(transaction_rows[0])
        material = "|".join(group_key)
        series_list.append(
            RecurringSeries(
                series_id=hashlib.sha256(material.encode("utf-8")).hexdigest()[:32],
                counterparty=label,
                transaction_type=group_key[1],
                frequency=frequency,
                average_amount=sum(amounts, Decimal("0")) / Decimal(len(amounts)),
                median_amount=median_amount,
                confidence=confidence,
                transaction_count=len(transaction_rows),
                transactions=transaction_rows,
            )
        )
    series_list.sort(key=lambda item: (item.transaction_count, item.median_amount), reverse=True)
    if not available:
        series_list = []
    metadata = build_insight_metadata(
        dataset,
        included=included,
        excluded=excluded,
        uncertain=uncertain,
        available=available,
        unavailable_reason=unavailable_reason,
    )
    return RecurringPatternsResult(
        available=available,
        dataset=dataset,
        series=series_list,
        metadata=metadata,
    )


def format_recurring_patterns(result: RecurringPatternsResult, language: str = "en") -> str:
    if not result.available:
        return render_message("query.insight.coverage_required", language)
    if not result.series:
        return render_message("query.insight.recurring_patterns.none", language)
    return render_message(
        "query.insight.recurring_patterns.found",
        language,
        {"count": len(result.series)},
    )


def build_recurring_patterns_surface(result: RecurringPatternsResult, language: str = "en") -> SurfaceView:
    if not result.available or result.dataset is None:
        return SurfaceView(mode=SurfaceViewMode.INSIGHT, lead_text=format_recurring_patterns(result, language))
    items: list[SurfaceItemView] = []
    for series in result.series[:5]:
        frequency = render_message(
            f"query.insight.recurring_patterns.frequency.{series.frequency}",  # type: ignore[arg-type]
            language,
        )
        label = render_message(
            "query.insight.recurring_patterns.item",
            language,
            {
                "frequency": frequency,
                "transaction_type": series.transaction_type,
                "amount": format_naira_compact(series.median_amount),
                "counterparty": series.counterparty,
            },
        )
        evidence = RecurringPatternsEvidenceSelection(
            basis=result.dataset.basis,
            series_id=series.series_id,
            effective_start=str(result.dataset.start_date),
            effective_end=str(result.dataset.end_date),
            transaction_ids=list(filter(None, (_transaction_id(row) for row in series.transactions)))[:20],
        )
        items.append(
            SurfaceItemView(
                id=series.series_id,
                label=label,
                amount=float(series.median_amount),
                count=series.transaction_count,
                payload=SelectionPayload(
                    selection_kind="summary_scope",
                    entity_type="recurring_series",
                    entity_id=series.series_id,
                    label=label,
                    insight_evidence=evidence,
                ),
            )
        )
    return SurfaceView(
        mode=SurfaceViewMode.INSIGHT,
        lead_text=format_recurring_patterns(result, language),
        items=items,
    )


def build_recurring_patterns_items(
    result: RecurringPatternsResult,
    language: str = "en",
) -> list[dict[str, Any]]:
    del language
    items: list[dict[str, Any]] = []
    for series in result.series[:5]:
        first_date = min(filter(None, (_effective_date(row) for row in series.transactions)), default=date.min)
        items.append(
            {
                "id": series.series_id,
                "description": series.counterparty,
                "amount": float(series.median_amount),
                "date": first_date,
                "metadata": {
                    "frequency": series.frequency,
                    "confidence": series.confidence,
                    "transaction_count": series.transaction_count,
                },
            }
        )
    return items


async def resolve_recurring_patterns_evidence(
    service: AnalysisService,
    contract: QueryRequest,
    evidence: RecurringPatternsEvidenceSelection,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> list[dict[str, Any]]:
    del language
    if not isinstance(contract.operation, AnalyzeOperation):
        raise ValueError("recurring evidence requires AnalyzeOperation")
    period = contract.operation.scope.period.model_copy(
        update={
            "start": date.fromisoformat(evidence.effective_start),
            "end": date.fromisoformat(evidence.effective_end),
        }
    )
    evidence_contract = contract.model_copy(
        deep=True,
        update={
            "operation": contract.operation.model_copy(
                update={"scope": contract.operation.scope.model_copy(update={"period": period})}
            )
        },
    )
    dataset = await service.load_dataset(
        evidence_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=evidence.basis,
        period_label="current",
    )
    ids = set(evidence.transaction_ids)
    return [
        evidence_item(row, fallback_id=f"recurring-{index}")
        for index, row in enumerate(dataset.rows, 1)
        if _transaction_id(row) in ids
    ]
