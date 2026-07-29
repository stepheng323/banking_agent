"""Executors for native analysis operations."""

from __future__ import annotations

from banking.transactions.query.insights.anomalies import (
    build_anomalies_items,
    build_anomalies_surface,
    execute_anomalies,
    format_anomalies,
    resolve_anomalies_evidence,
)
from banking.transactions.query.insights.cash_flow_quality import (
    build_cash_flow_quality_items,
    build_cash_flow_quality_surface,
    execute_cash_flow_quality,
    format_cash_flow_quality,
)
from banking.transactions.query.insights.counterparty_concentration import (
    build_counterparty_concentration_items,
    build_counterparty_concentration_surface,
    execute_counterparty_concentration,
    format_counterparty_concentration,
    resolve_counterparty_concentration_evidence,
)
from banking.transactions.query.insights.forecast import (
    build_forecast_items,
    build_forecast_surface,
    execute_forecast,
    format_forecast,
)
from banking.transactions.query.insights.probable_duplicates import (
    build_probable_duplicates_items,
    build_probable_duplicates_surface,
    execute_probable_duplicates,
    format_probable_duplicates,
    resolve_probable_duplicates_evidence,
)
from banking.transactions.query.insights.recurring_patterns import (
    build_recurring_patterns_items,
    build_recurring_patterns_surface,
    execute_recurring_patterns,
    format_recurring_patterns,
    resolve_recurring_patterns_evidence,
)
from banking.transactions.query.insights.registry import (
    InsightDefinition,
    build_presentation,
    insight_registry,
)
from banking.transactions.query.insights.runway import (
    build_runway_items,
    build_runway_surface,
    execute_runway,
    format_runway,
)
from banking.transactions.query.insights.variance import execute_variance_drivers
from banking.transactions.query.insights.variance_evidence import resolve_variance_evidence
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    AnomaliesSpec,
    CashFlowQualitySpec,
    CounterpartyConcentrationSpec,
    ForecastSpec,
    ProbableDuplicatesSpec,
    RecurringPatternsSpec,
    RunwaySpec,
    VarianceDriversSpec,
)
from banking.transactions.query.presentation.insights.variance import (
    build_variance_query_items,
    build_variance_surface_view,
    format_variance_result,
)
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnomaliesResult,
    CashFlowQualityResult,
    CounterpartyConcentrationResult,
    ForecastResult,
    InsightResultMetadata,
    ProbableDuplicatesResult,
    RecurringPatternsResult,
    RunwayResult,
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
            ),
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

    result = await execute_variance_drivers(
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
    return result.model_copy(
        update={
            "metadata": InsightResultMetadata(
                basis=basis,
                effective_start=current_dataset.start_date,
                effective_end=current_dataset.end_date,
                coverage=result.coverage,
                excluded_value=result.excluded_value,
                uncertain_value=result.uncertainty,
                uncertain_count=current_dataset.unresolved_count,
                available=result.available,
                unavailable_reason=result.unavailable_reason,
            )
        }
    )


insight_registry.register(
    InsightDefinition(
        insight_type="variance_drivers",
        spec_type=VarianceDriversSpec,
        result_type=VarianceAnalysisResult,
        executor=execute_variance,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_variance_result,
            surface_builder=build_variance_surface_view,
            item_builder=build_variance_query_items,
        ),
        evidence_resolver=resolve_variance_evidence,
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="recurring_patterns",
        spec_type=RecurringPatternsSpec,
        result_type=RecurringPatternsResult,
        executor=execute_recurring_patterns,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_recurring_patterns,
            surface_builder=build_recurring_patterns_surface,
            item_builder=build_recurring_patterns_items,
        ),
        evidence_resolver=resolve_recurring_patterns_evidence,
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="anomalies",
        spec_type=AnomaliesSpec,
        result_type=AnomaliesResult,
        executor=execute_anomalies,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_anomalies,
            surface_builder=build_anomalies_surface,
            item_builder=build_anomalies_items,
        ),
        evidence_resolver=resolve_anomalies_evidence,
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="counterparty_concentration",
        spec_type=CounterpartyConcentrationSpec,
        result_type=CounterpartyConcentrationResult,
        executor=execute_counterparty_concentration,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_counterparty_concentration,
            surface_builder=build_counterparty_concentration_surface,
            item_builder=build_counterparty_concentration_items,
        ),
        evidence_resolver=resolve_counterparty_concentration_evidence,
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="probable_duplicates",
        spec_type=ProbableDuplicatesSpec,
        result_type=ProbableDuplicatesResult,
        executor=execute_probable_duplicates,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_probable_duplicates,
            surface_builder=build_probable_duplicates_surface,
            item_builder=build_probable_duplicates_items,
        ),
        evidence_resolver=resolve_probable_duplicates_evidence,
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="forecast",
        spec_type=ForecastSpec,
        result_type=ForecastResult,
        executor=execute_forecast,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_forecast,
            surface_builder=build_forecast_surface,
            item_builder=build_forecast_items,
        ),
        evidence_resolver=None,
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="runway",
        spec_type=RunwaySpec,
        result_type=RunwayResult,
        executor=execute_runway,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_runway,
            surface_builder=build_runway_surface,
            item_builder=build_runway_items,
        ),
        evidence_resolver=None,
    )
)

insight_registry.register(
    InsightDefinition(
        insight_type="cash_flow_quality",
        spec_type=CashFlowQualitySpec,
        result_type=CashFlowQualityResult,
        executor=execute_cash_flow_quality,
        presenter=lambda result, language: build_presentation(
            result,
            language,
            formatter=format_cash_flow_quality,
            surface_builder=build_cash_flow_quality_surface,
            item_builder=build_cash_flow_quality_items,
        ),
        evidence_resolver=None,
    )
)
