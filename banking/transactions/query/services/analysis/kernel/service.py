"""Shared financial-analysis service facade."""

from __future__ import annotations

from typing import Any

from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisBasis,
    AnalysisDataset,
    AnalysisMetric,
    CashFlowAnalysis,
    ComparisonSpec,
    Dimension,
    MetricResult,
    MetricSpec,
    VarianceAnalysisResult,
)
from banking.transactions.query.services.analysis.kernel.metrics import (
    analyze_cash_flow,
    analyze_variance,
    calculate_breakdowns,
    calculate_metric,
    compare_periods,
)
from banking.transactions.query.services.analysis.kernel.source import (
    AnalysisDataSource,
    build_analysis_source,
)
from shared.clients.abstractions.banking import BankDataProvider


class AnalysisService:
    """Shared kernel for financial analysis across query intents."""

    def __init__(self, provider: BankDataProvider):
        self.provider = provider

    def _get_source(self, basis: AnalysisBasis) -> AnalysisDataSource:
        return build_analysis_source(basis, provider=self.provider)

    async def load_dataset(
        self,
        contract: QueryRequest,
        account_id: str,
        account_ids: list[str],
        accounts_info: list[dict] | None,
        *,
        user_id: str | None,
        basis: AnalysisBasis,
        period_label: str,
    ) -> AnalysisDataset:
        source = self._get_source(basis)
        return await source.load(
            contract=contract,
            account_id=account_id,
            account_ids=account_ids,
            accounts_info=accounts_info,
            user_id=user_id,
            period_label=period_label,
        )

    async def calculate_metric(
        self,
        dataset: AnalysisDataset,
        metric: AnalysisMetric,
        *,
        dimensions: list[Dimension] | None = None,
        confidence_policy: str = "segment_uncertain",
        completeness_policy: str = "disclose",
    ) -> MetricResult:
        spec = MetricSpec(
            metric=metric,
            basis=dataset.basis,
            dimensions=list(dimensions or []),
            confidence_policy=confidence_policy,  # type: ignore[arg-type]
            completeness_policy=completeness_policy,  # type: ignore[arg-type]
        )
        return calculate_metric(dataset, spec)

    async def calculate_breakdowns(
        self,
        dataset: AnalysisDataset,
        metric: AnalysisMetric,
        *,
        dimensions: list[Dimension],
        confidence_policy: str = "segment_uncertain",
        completeness_policy: str = "disclose",
    ) -> list[Any]:
        spec = MetricSpec(
            metric=metric,
            basis=dataset.basis,
            dimensions=dimensions,
            confidence_policy=confidence_policy,  # type: ignore[arg-type]
            completeness_policy=completeness_policy,  # type: ignore[arg-type]
        )
        return calculate_breakdowns(dataset, spec)

    async def compare_periods(
        self,
        metric: AnalysisMetric,
        current_dataset: AnalysisDataset,
        baseline_dataset: AnalysisDataset,
        *,
        dimensions: list[Dimension] | None = None,
        confidence_policy: str = "segment_uncertain",
        completeness_policy: str = "disclose",
    ) -> Any:
        current_spec = MetricSpec(
            metric=metric,
            basis=current_dataset.basis,
            dimensions=list(dimensions or []),
            confidence_policy=confidence_policy,  # type: ignore[arg-type]
            completeness_policy=completeness_policy,  # type: ignore[arg-type]
        )
        baseline_spec = current_spec.model_copy(update={"basis": baseline_dataset.basis})
        current_metric = calculate_metric(current_dataset, current_spec)
        baseline_metric = calculate_metric(baseline_dataset, baseline_spec)
        return compare_periods(current_metric, baseline_metric)

    async def analyze_variance(
        self,
        current_dataset: AnalysisDataset,
        baseline_dataset: AnalysisDataset,
        metric: AnalysisMetric,
        *,
        dimensions: list[Dimension],
        confidence_policy: str = "segment_uncertain",
        completeness_policy: str = "disclose",
        evidence_limit: int = 5,
    ) -> VarianceAnalysisResult:
        spec = ComparisonSpec(
            metric=metric,
            basis=current_dataset.basis,
            current=current_dataset,
            baseline=baseline_dataset,
            dimensions=dimensions,
            confidence_policy=confidence_policy,  # type: ignore[arg-type]
            completeness_policy=completeness_policy,  # type: ignore[arg-type]
            evidence_limit=evidence_limit,
        )
        return analyze_variance(current_dataset, baseline_dataset, spec)

    async def analyze_cash_flow(self, dataset: AnalysisDataset) -> CashFlowAnalysis:
        """Return the canonical cash-flow truth for one loaded dataset."""
        return analyze_cash_flow(dataset)
