"""Variance-drivers executor built on the shared analysis kernel."""

from __future__ import annotations

from typing import Literal

from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisBasis,
    AnalysisDataset,
    AnalysisMetric,
    ComparisonSpec,
    Dimension,
    InsightEvidenceSelector,
    PeriodComparison,
    VarianceAnalysisResult,
    VarianceDimensionView,
    VarianceDriver,
)
from banking.transactions.query.services.analysis.kernel.metrics import (
    analyze_variance,
    combine_coverage_status,
)

_MEASURE_TO_METRIC: dict[str, AnalysisMetric] = {
    "spending": AnalysisMetric.SPENDING,
    "income": AnalysisMetric.INCOME,
    "net_cash_flow": AnalysisMetric.NET_CASH_FLOW,
}

_MEASURE_OVERVIEW_METRICS: list[AnalysisMetric] = [
    AnalysisMetric.INCOME,
    AnalysisMetric.SPENDING,
    AnalysisMetric.NET_CASH_FLOW,
]


def _dimension_from_string(value: str) -> Dimension:
    return Dimension(value)


def _metric_to_measure(metric: AnalysisMetric) -> Literal["spending", "income", "net_cash_flow"]:
    mapping: dict[AnalysisMetric, Literal["spending", "income", "net_cash_flow"]] = {
        AnalysisMetric.SPENDING: "spending",
        AnalysisMetric.INCOME: "income",
        AnalysisMetric.NET_CASH_FLOW: "net_cash_flow",
    }
    return mapping.get(metric, "spending")


def _build_evidence_selector(
    measure: str,
    dimension: Dimension,
    driver: VarianceDriver,
    basis: AnalysisBasis,
    current_dataset: AnalysisDataset,
    baseline_dataset: AnalysisDataset,
    metric: AnalysisMetric,
) -> InsightEvidenceSelector:
    return InsightEvidenceSelector(
        measure=measure,
        dimension=dimension,
        bucket_key=driver.key,
        basis=basis,
        current_start=current_dataset.start_date,
        current_end=current_dataset.end_date,
        baseline_start=baseline_dataset.start_date,
        baseline_end=baseline_dataset.end_date,
        metric=metric,
        filters={"dimension": dimension.value, "key": driver.key},
    )


async def execute_variance_drivers(
    *,
    current_dataset: AnalysisDataset,
    baseline_dataset: AnalysisDataset,
    measure: str,
    dimensions: list[str],
    basis: AnalysisBasis,
    confidence_policy: str,
    completeness_policy: str,
    evidence_limit: int,
    language: str = "en",
) -> VarianceAnalysisResult:
    """Execute variance analysis for a single metric or cash-flow overview."""
    parsed_dimensions = [_dimension_from_string(d) for d in dimensions]

    if measure == "cash_flow_overview":
        return await _execute_cash_flow_overview(
            current_dataset=current_dataset,
            baseline_dataset=baseline_dataset,
            basis=basis,
            dimensions=parsed_dimensions,
            confidence_policy=confidence_policy,
            completeness_policy=completeness_policy,
            evidence_limit=evidence_limit,
            language=language,
        )

    metric = _MEASURE_TO_METRIC[measure]
    return await _execute_single_metric_variance(
        current_dataset=current_dataset,
        baseline_dataset=baseline_dataset,
        metric=metric,
        basis=basis,
        dimensions=parsed_dimensions,
        confidence_policy=confidence_policy,
        completeness_policy=completeness_policy,
        evidence_limit=evidence_limit,
        language=language,
    )


async def _execute_single_metric_variance(
    current_dataset: AnalysisDataset,
    baseline_dataset: AnalysisDataset,
    metric: AnalysisMetric,
    basis: AnalysisBasis,
    dimensions: list[Dimension],
    confidence_policy: str,
    completeness_policy: str,
    evidence_limit: int,
    language: str = "en",
) -> VarianceAnalysisResult:
    spec = ComparisonSpec(
        metric=metric,
        basis=basis,
        current=current_dataset,
        baseline=baseline_dataset,
        dimensions=dimensions,
        confidence_policy=confidence_policy,  # type: ignore[arg-type]
        completeness_policy=completeness_policy,  # type: ignore[arg-type]
        evidence_limit=evidence_limit,
    )
    result = analyze_variance(current_dataset, baseline_dataset, spec, language=language)
    selectors = _collect_evidence_selectors(
        measure=_metric_to_measure(metric),
        basis=basis,
        current_dataset=current_dataset,
        baseline_dataset=baseline_dataset,
        metric=metric,
        dimension_views=result.dimension_views,
    )
    return result.model_copy(
        update={
            "measure": _metric_to_measure(metric),
            "evidence_selectors": selectors,
        }
    )


async def _execute_cash_flow_overview(
    current_dataset: AnalysisDataset,
    baseline_dataset: AnalysisDataset,
    basis: AnalysisBasis,
    dimensions: list[Dimension],
    confidence_policy: str,
    completeness_policy: str,
    evidence_limit: int,
    language: str = "en",
) -> VarianceAnalysisResult:
    """Variance composition over income, spending, and net operating cash flow."""
    metric_comparisons: list[PeriodComparison] = []
    dimension_views: list[VarianceDimensionView] = []
    selectors: list[InsightEvidenceSelector] = []
    results_by_metric: dict[AnalysisMetric, VarianceAnalysisResult] = {}

    for metric in _MEASURE_OVERVIEW_METRICS:
        metric_spec = ComparisonSpec(
            metric=metric,
            basis=basis,
            current=current_dataset,
            baseline=baseline_dataset,
            dimensions=dimensions,
            confidence_policy=confidence_policy,  # type: ignore[arg-type]
            completeness_policy=completeness_policy,  # type: ignore[arg-type]
            evidence_limit=evidence_limit,
        )
        metric_result = analyze_variance(current_dataset, baseline_dataset, metric_spec, language=language)
        results_by_metric[metric] = metric_result
        metric_comparisons.extend(metric_result.metric_comparisons)
        dimension_views.extend(metric_result.dimension_views)
        selectors.extend(
            _collect_evidence_selectors(
                measure="cash_flow_overview",
                basis=basis,
                current_dataset=current_dataset,
                baseline_dataset=baseline_dataset,
                metric=metric,
                dimension_views=metric_result.dimension_views,
            )
        )

    net_result = results_by_metric[AnalysisMetric.NET_CASH_FLOW]
    coverage = combine_coverage_status(current_dataset.coverage_status, baseline_dataset.coverage_status)

    return VarianceAnalysisResult(
        measure="cash_flow_overview",
        basis=basis,
        current_period=current_dataset,
        baseline_period=baseline_dataset,
        metric_comparisons=metric_comparisons,
        dimension_views=dimension_views,
        residual=net_result.residual,
        # Net includes every eligible direction once, so it is the unique-row
        # disclosure source for overview uncertainty and exclusions.
        uncertainty=net_result.uncertainty,
        coverage=coverage,
        unresolved_value=net_result.unresolved_value,
        excluded_value=net_result.excluded_value,
        evidence_selectors=selectors,
        available=all(result.available for result in results_by_metric.values()),
        unavailable_reason=(
            "coverage_incomplete" if any(not result.available for result in results_by_metric.values()) else None
        ),
    )


def _collect_evidence_selectors(
    measure: str,
    basis: AnalysisBasis,
    current_dataset: AnalysisDataset,
    baseline_dataset: AnalysisDataset,
    metric: AnalysisMetric,
    dimension_views: list[VarianceDimensionView],
) -> list[InsightEvidenceSelector]:
    selectors: list[InsightEvidenceSelector] = []
    for view in dimension_views:
        for driver in view.drivers:
            selectors.append(
                _build_evidence_selector(
                    measure=measure,
                    dimension=view.dimension,
                    driver=driver,
                    basis=basis,
                    current_dataset=current_dataset,
                    baseline_dataset=baseline_dataset,
                    metric=metric,
                )
            )
    return selectors
