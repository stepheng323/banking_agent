"""Executors for native analysis operations."""

from __future__ import annotations

from banking.transactions.query.insights.probable_duplicates import (
    build_probable_duplicates_items,
    build_probable_duplicates_surface,
    execute_probable_duplicates,
    format_probable_duplicates,
    resolve_probable_duplicates_evidence,
)
from banking.transactions.query.insights.registry import InsightDefinition, insight_registry
from banking.transactions.query.insights.variance import execute_variance_drivers
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.extraction import ProbableDuplicatesSpec, VarianceDriversSpec
from banking.transactions.query.models.operations import AnalyzeOperation
from banking.transactions.query.presentation.insights.variance import (
    build_variance_query_items,
    build_variance_surface_view,
    format_variance_result,
)
from banking.transactions.query.services.analysis.kernel.contracts import (
    ProbableDuplicatesResult,
    VarianceAnalysisResult,
)
from banking.transactions.query.services.analysis.kernel.service import AnalysisService


async def execute_variance(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> VarianceAnalysisResult:
    """Dispatch variance-drivers execution through the shared kernel."""
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation):
        raise ValueError("variance execution requires an AnalyzeOperation")
    analysis = operation.analysis
    if not isinstance(analysis, VarianceDriversSpec):
        raise ValueError("variance execution requires a VarianceDriversSpec")

    basis = analysis.analysis_basis
    current_dataset = await service.load_dataset(
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=basis,
        period_label="current",
    )

    current_range = operation.scope.period

    from banking.transactions.query.handlers.time_comparison import _get_comparison_period

    baseline_range = _get_comparison_period(current_range, baseline=analysis.baseline)
    baseline_contract = contract.model_copy(
        update={
            "operation": operation.model_copy(
                update={"scope": operation.scope.model_copy(update={"period": baseline_range})}
            )
        },
        deep=True,
    )

    baseline_dataset = await service.load_dataset(
        baseline_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=basis,
        period_label="baseline",
    )

    return await execute_variance_drivers(
        current_dataset=current_dataset,
        baseline_dataset=baseline_dataset,
        measure=analysis.measure,
        dimensions=list(analysis.dimensions),
        basis=basis,
        confidence_policy=analysis.confidence_policy,
        completeness_policy=analysis.completeness_policy,
        evidence_limit=analysis.evidence_limit,
        language=language,
    )


insight_registry.register(
    InsightDefinition(
        insight_type="variance_drivers",
        spec_type=VarianceDriversSpec,
        result_type=VarianceAnalysisResult,
        executor=execute_variance,
        formatter=format_variance_result,
        surface_builder=build_variance_surface_view,
        item_builder=build_variance_query_items,
        evidence_resolver=None,  # Variance currently doesn't implement a custom resolver
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="probable_duplicates",
        spec_type=ProbableDuplicatesSpec,
        result_type=ProbableDuplicatesResult,
        executor=execute_probable_duplicates,
        formatter=format_probable_duplicates,
        surface_builder=build_probable_duplicates_surface,
        item_builder=build_probable_duplicates_items,
        evidence_resolver=resolve_probable_duplicates_evidence,
    )
)
