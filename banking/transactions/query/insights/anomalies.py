"""Robust deterministic transaction-anomaly insight."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import (
    AnomaliesEvidenceSelection,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation, AnomaliesSpec
from banking.transactions.query.services.analysis.kernel.contracts import AnomaliesResult, AnomalyCandidate
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
)
from banking.transactions.query.services.analysis.kernel.service import AnalysisService

_MINIMUM_DEVIATION_BY_CURRENCY = {
    "NGN": Decimal("1000"),
    "USD": Decimal("1"),
    "GBP": Decimal("1"),
    "EUR": Decimal("1"),
}


def _transaction_id(row: dict[str, Any]) -> str:
    return str(row.get("transaction_id") or row.get("id") or "").strip()


def _quartiles(values: list[Decimal]) -> tuple[Decimal, Decimal]:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    lower = ordered[:midpoint]
    upper = ordered[midpoint + (len(ordered) % 2) :]
    return Decimal(str(median(lower))), Decimal(str(median(upper)))


def _robust_score(amount: Decimal, baseline: list[Decimal]) -> tuple[Decimal, float]:
    baseline_median = Decimal(str(median(baseline)))
    deviations = [abs(value - baseline_median) for value in baseline]
    mad = Decimal(str(median(deviations)))
    difference = amount - baseline_median
    if mad > 0:
        return baseline_median, float(Decimal("0.6745") * difference / mad)
    q1, q3 = _quartiles(baseline)
    iqr = q3 - q1
    if iqr > 0:
        return baseline_median, float(difference / iqr)
    return baseline_median, float(difference / baseline_median) if baseline_median > 0 else 0.0


def _candidate_for_row(
    row: dict[str, Any],
    *,
    comparison_rows: list[dict[str, Any]],
    dimension_name: str,
    dimension_value: str,
) -> AnomalyCandidate | None:
    amount = row_amount(row)
    baseline_amounts = [row_amount(candidate) for candidate in comparison_rows]
    baseline, score = _robust_score(amount, baseline_amounts)
    if baseline <= 0 or amount <= baseline:
        return None
    ratio = amount / baseline
    minimum_deviation = _MINIMUM_DEVIATION_BY_CURRENCY.get(row_currency(row), Decimal("1"))
    if amount - baseline < minimum_deviation or ratio < Decimal("3") or score < 3.5:
        return None
    transaction_id = _transaction_id(row)
    material = f"{dimension_name}|{dimension_value}|{transaction_id}"
    return AnomalyCandidate(
        anomaly_id=hashlib.sha256(material.encode("utf-8")).hexdigest()[:32],
        transaction=row,
        baseline_average=baseline,
        multiplier=float(ratio),
        robust_score=score,
        metric="income" if row_direction(row) == "credit" else "spending",
        dimension_name=dimension_name,
        dimension_value=dimension_value,
    )


async def execute_anomalies(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> AnomaliesResult:
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation) or not isinstance(operation.analysis, AnomaliesSpec):
        raise ValueError("anomaly execution requires AnomaliesSpec")
    analysis = operation.analysis
    start, end = inclusive_window(end=operation.scope.period.end, days=analysis.baseline_days)
    expanded_contract = contract.model_copy(
        deep=True,
        update={
            "operation": operation.model_copy(
                update={"scope": operation.scope.model_copy(update={"period": operation.scope.period.model_copy(
                    update={"start": start, "end": end}
                )})}
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
    covered_days = dataset.fully_covered_days or 0
    if covered_days < analysis.min_covered_days:
        available, unavailable_reason = False, "insufficient_covered_days"

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in included:
        direction = row_direction(row)
        if direction not in {"credit", "debit"}:
            excluded.append(row)
            continue
        category = str(row.get("resolved_category") or row.get("category") or "").strip().lower()
        counterparty = row_counterparty_key(row)
        if category:
            groups[(direction, "category", category)].append(row)
        if counterparty:
            groups[(direction, "counterparty", counterparty)].append(row)

    candidates: dict[str, AnomalyCandidate] = {}
    for (_direction, dimension_name, dimension_value), rows in groups.items():
        if len(rows) < analysis.min_comparable_observations + 1:
            continue
        for row in rows:
            comparison_rows = [candidate for candidate in rows if candidate is not row]
            candidate = _candidate_for_row(
                row,
                comparison_rows=comparison_rows,
                dimension_name=dimension_name,
                dimension_value=dimension_value,
            )
            if candidate is None:
                continue
            transaction_id = _transaction_id(row) or candidate.anomaly_id
            previous = candidates.get(transaction_id)
            if previous is None or candidate.robust_score > previous.robust_score:
                candidates[transaction_id] = candidate
    anomalies = sorted(candidates.values(), key=lambda item: item.robust_score, reverse=True)
    if not available:
        anomalies = []
    metadata = build_insight_metadata(
        dataset,
        included=included,
        excluded=excluded,
        uncertain=uncertain,
        available=available,
        unavailable_reason=unavailable_reason,
    )
    return AnomaliesResult(
        available=available,
        dataset=dataset,
        anomalies=anomalies,
        metadata=metadata,
    )


def format_anomalies(result: AnomaliesResult, language: str = "en") -> str:
    if not result.available:
        return render_message("query.insight.coverage_required", language)
    if not result.anomalies:
        return render_message("query.insight.anomalies.none", language)
    spending_count = sum(item.metric == "spending" for item in result.anomalies)
    income_count = len(result.anomalies) - spending_count
    return render_message(
        "query.insight.anomalies.found",
        language,
        {"count": len(result.anomalies), "spending_count": spending_count, "income_count": income_count},
    )


def build_anomalies_surface(result: AnomaliesResult, language: str = "en") -> SurfaceView:
    if not result.available or result.dataset is None:
        return SurfaceView(mode=SurfaceViewMode.INSIGHT, lead_text=format_anomalies(result, language))
    items: list[SurfaceItemView] = []
    for candidate in result.anomalies[:5]:
        row = candidate.transaction
        label = render_message(
            "query.insight.anomalies.item",
            language,
            {
                "counterparty": row_counterparty_label(row) or candidate.dimension_value,
                "amount": format_naira_compact(row_amount(row)),
                "multiplier": f"{candidate.multiplier:.1f}",
            },
        )
        transaction_id = _transaction_id(row)
        evidence = AnomaliesEvidenceSelection(
            basis=result.dataset.basis,
            anomaly_id=candidate.anomaly_id,
            effective_start=str(result.dataset.start_date),
            effective_end=str(result.dataset.end_date),
            transaction_id=transaction_id,
        )
        items.append(
            SurfaceItemView(
                id=candidate.anomaly_id,
                label=label,
                amount=float(row_amount(row)),
                count=1,
                payload=SelectionPayload(
                    selection_kind="summary_scope",
                    entity_type="transaction_anomaly",
                    entity_id=candidate.anomaly_id,
                    label=label,
                    insight_evidence=evidence,
                ),
            )
        )
    return SurfaceView(mode=SurfaceViewMode.INSIGHT, lead_text=format_anomalies(result, language), items=items)


def build_anomalies_items(result: AnomaliesResult, language: str = "en") -> list[dict[str, Any]]:
    del language
    items: list[dict[str, Any]] = []
    for candidate in result.anomalies[:5]:
        row = candidate.transaction
        effective_at = row_effective_datetime(row)
        items.append(
            {
                "id": candidate.anomaly_id,
                "description": row_counterparty_label(row) or candidate.dimension_value,
                "amount": float(row_amount(row)),
                "date": effective_at.date() if effective_at is not None else date.min,
                "metadata": {
                    "metric": candidate.metric,
                    "multiplier": candidate.multiplier,
                    "robust_score": candidate.robust_score,
                },
            }
        )
    return items


async def resolve_anomalies_evidence(
    service: AnalysisService,
    contract: QueryRequest,
    evidence: AnomaliesEvidenceSelection,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> list[dict[str, Any]]:
    del language
    if not isinstance(contract.operation, AnalyzeOperation):
        raise ValueError("anomaly evidence requires AnalyzeOperation")
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
    return [
        evidence_item(row, fallback_id="anomaly")
        for row in dataset.rows
        if _transaction_id(row) == evidence.transaction_id
    ]
