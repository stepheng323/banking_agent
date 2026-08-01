"""Counterparty concentration over canonical financial metrics."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import (
    CounterpartyConcentrationEvidenceSelection,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation, CounterpartyConcentrationSpec
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisMetric,
    ConcentrationGroup,
    ConfidencePolicy,
    CounterpartyConcentrationResult,
    Dimension,
    InsightResultMetadata,
    MetricSpec,
)
from banking.transactions.query.services.analysis.kernel.insight_support import evidence_item
from banking.transactions.query.services.analysis.kernel.metrics import (
    calculate_breakdowns,
    calculate_metric,
    filter_dimension_rows,
    row_effective_datetime,
)
from banking.transactions.query.services.analysis.kernel.service import AnalysisService

_MEASURES = {
    "spending": AnalysisMetric.SPENDING,
    "income": AnalysisMetric.INCOME,
    "inflow": AnalysisMetric.INFLOW,
    "outflow": AnalysisMetric.OUTFLOW,
}


def _metric_spec(analysis: CounterpartyConcentrationSpec) -> MetricSpec:
    return MetricSpec(
        metric=_MEASURES[analysis.measure],
        basis=analysis.analysis_basis,
        dimensions=[Dimension.COUNTERPARTY],
        confidence_policy=ConfidencePolicy(analysis.confidence_policy),
        completeness_policy=analysis.completeness_policy,
        exclude_internal=True,
        exclude_financing_investing_for_operating=True,
    )


async def execute_counterparty_concentration(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> CounterpartyConcentrationResult:
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation) or not isinstance(
        operation.analysis,
        CounterpartyConcentrationSpec,
    ):
        raise ValueError("concentration execution requires CounterpartyConcentrationSpec")
    analysis = operation.analysis
    dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=analysis.analysis_basis,
        period_label="current",
    )
    spec = _metric_spec(analysis)
    metric = calculate_metric(dataset, spec)
    breakdowns = calculate_breakdowns(dataset, spec, language=language)
    breakdown = breakdowns[0] if breakdowns else None
    available = metric.available and dataset.coverage_status.value != "unavailable"
    total = breakdown.total if breakdown is not None else Decimal("0")
    unresolved = breakdown.unresolved_bucket.value if breakdown and breakdown.unresolved_bucket else Decimal("0")
    denominator = total.copy_abs()
    groups: list[ConcentrationGroup] = []
    if available and breakdown is not None and denominator > 0:
        for bucket in breakdown.buckets:
            rows = filter_dimension_rows(
                dataset,
                spec,
                dimension=Dimension.COUNTERPARTY,
                bucket_key=bucket.key,
            )
            groups.append(
                ConcentrationGroup(
                    key=bucket.key,
                    counterparty=bucket.label or bucket.key,
                    value=bucket.value.copy_abs(),
                    percentage=(bucket.value.copy_abs() / denominator) * Decimal("100"),
                    transactions=rows,
                )
            )
    top_groups = groups[:5]
    top_share = sum((group.percentage for group in top_groups), Decimal("0"))
    metadata = InsightResultMetadata(
        basis=dataset.basis,
        effective_start=dataset.start_date,
        effective_end=dataset.end_date,
        coverage=dataset.coverage_status,
        included_count=metric.count,
        included_value=metric.included_value,
        excluded_count=metric.excluded_count,
        excluded_value=metric.excluded_value,
        uncertain_count=metric.uncertain_count,
        uncertain_value=metric.uncertain_value,
        available=available,
        unavailable_reason=metric.unavailable_reason or ("coverage_unavailable" if not available else None),
    )
    return CounterpartyConcentrationResult(
        available=available,
        dataset=dataset,
        measure=analysis.measure,
        groups=top_groups,
        total_value=denominator,
        unresolved_value=unresolved.copy_abs(),
        residual_percentage=max(Decimal("0"), Decimal("100") - top_share),
        metadata=metadata,
    )


def format_counterparty_concentration(
    result: CounterpartyConcentrationResult,
    language: str = "en",
) -> str:
    if not result.available:
        return render_message("query.insight.coverage_required", language)
    if not result.groups:
        return render_message("query.insight.counterparty_concentration.none", language)
    top = result.groups[0]
    return render_message(
        "query.insight.counterparty_concentration.found",
        language,
        {
            "measure": result.measure,
            "counterparty": top.counterparty,
            "percentage": f"{top.percentage:.1f}",
        },
    )


def build_counterparty_concentration_surface(
    result: CounterpartyConcentrationResult,
    language: str = "en",
) -> SurfaceView:
    if not result.available or result.dataset is None or result.measure is None:
        return SurfaceView(
            mode=SurfaceViewMode.INSIGHT,
            lead_text=format_counterparty_concentration(result, language),
        )
    items: list[SurfaceItemView] = []
    for index, group in enumerate(result.groups):
        label = render_message(
            "query.insight.counterparty_concentration.item",
            language,
            {
                "counterparty": group.counterparty,
                "amount": format_naira_compact(group.value),
                "percentage": f"{group.percentage:.1f}",
            },
        )
        evidence = CounterpartyConcentrationEvidenceSelection(
            basis=result.dataset.basis,
            counterparty_key=group.key,
            measure=result.measure,
            effective_start=str(result.dataset.start_date),
            effective_end=str(result.dataset.end_date),
        )
        items.append(
            SurfaceItemView(
                id=f"concentration-{index + 1}",
                label=label,
                amount=float(group.value),
                count=len(group.transactions),
                payload=SelectionPayload(
                    selection_kind="summary_scope",
                    entity_type="counterparty_concentration",
                    entity_id=f"concentration-{index + 1}",
                    label=label,
                    insight_evidence=evidence,
                ),
            )
        )
    return SurfaceView(
        mode=SurfaceViewMode.INSIGHT,
        lead_text=format_counterparty_concentration(result, language),
        items=items,
    )


def build_counterparty_concentration_items(
    result: CounterpartyConcentrationResult,
    language: str = "en",
) -> list[dict[str, Any]]:
    del language
    items: list[dict[str, Any]] = []
    for index, group in enumerate(result.groups):
        dates = [
            effective.date() for row in group.transactions if (effective := row_effective_datetime(row)) is not None
        ]
        items.append(
            {
                "id": f"concentration-{index + 1}",
                "description": group.counterparty,
                "amount": float(group.value),
                "date": max(dates, default=date.min),
                "metadata": {
                    "percentage": float(group.percentage),
                    "transaction_count": len(group.transactions),
                },
            }
        )
    return items


async def resolve_counterparty_concentration_evidence(
    service: AnalysisService,
    contract: QueryRequest,
    evidence: CounterpartyConcentrationEvidenceSelection,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> list[dict[str, Any]]:
    del language
    if not isinstance(contract.operation, AnalyzeOperation):
        raise ValueError("concentration evidence requires AnalyzeOperation")
    period = contract.operation.scope.period.model_copy(
        update={
            "start": date.fromisoformat(evidence.effective_start),
            "end": date.fromisoformat(evidence.effective_end),
        }
    )
    analysis = contract.operation.analysis
    if not isinstance(analysis, CounterpartyConcentrationSpec):
        raise ValueError("concentration evidence requires CounterpartyConcentrationSpec")
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
    spec = _metric_spec(analysis.model_copy(update={"measure": evidence.measure}))
    rows = filter_dimension_rows(
        dataset,
        spec,
        dimension=Dimension.COUNTERPARTY,
        bucket_key=evidence.counterparty_key,
    )
    return [evidence_item(row, fallback_id=f"concentration-{index}") for index, row in enumerate(rows, 1)]
