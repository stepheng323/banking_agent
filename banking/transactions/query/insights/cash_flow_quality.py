"""Coverage-aware cash-flow quality across complete calendar months."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation, CashFlowQualitySpec
from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisMetric,
    CashFlowQualityResult,
    MetricSpec,
)
from banking.transactions.query.services.analysis.kernel.insight_support import (
    build_insight_metadata,
    filter_insight_rows,
    insight_available,
)
from banking.transactions.query.services.analysis.kernel.metrics import calculate_metric
from banking.transactions.query.services.analysis.kernel.service import AnalysisService


def _subtract_months(value: date, months: int) -> date:
    total_months = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(total_months, 12)
    return date(year, month_index + 1, 1)


async def execute_cash_flow_quality(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> CashFlowQualityResult:
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation) or not isinstance(operation.analysis, CashFlowQualitySpec):
        raise ValueError("cash-flow-quality execution requires CashFlowQualitySpec")
    analysis = operation.analysis
    current_month_start = operation.scope.period.end.replace(day=1)
    history_start = _subtract_months(current_month_start, analysis.min_complete_months)
    history_end = current_month_start - timedelta(days=1)
    modified_contract = contract.model_copy(
        deep=True,
        update={
            "operation": operation.model_copy(
                update={"scope": operation.scope.model_copy(update={"period": operation.scope.period.model_copy(
                    update={"start": history_start, "end": history_end}
                )})}
            )
        },
    )
    dataset = await service.load_dataset(
        modified_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=analysis.analysis_basis,
        period_label="history",
    )
    available, unavailable_reason = insight_available(
        dataset,
        completeness_policy=analysis.completeness_policy,
    )
    expected_days = (history_end - history_start).days + 1
    # A health label is meaningful only when every selected account covers
    # every complete month in the requested history.
    if dataset.coverage_status.value != "complete" or (dataset.fully_covered_days or 0) < expected_days:
        available, unavailable_reason = False, "insufficient_complete_months"
    common = {
        "basis": analysis.analysis_basis,
        "confidence_policy": analysis.confidence_policy,
        "completeness_policy": analysis.completeness_policy,
        "exclude_internal": True,
        "exclude_financing_investing_for_operating": True,
    }
    income = calculate_metric(dataset, MetricSpec(metric=AnalysisMetric.INCOME, **common))  # type: ignore[arg-type]
    spending = calculate_metric(dataset, MetricSpec(metric=AnalysisMetric.SPENDING, **common))  # type: ignore[arg-type]
    available = available and income.available and spending.available
    if not available and unavailable_reason is None:
        unavailable_reason = income.unavailable_reason or spending.unavailable_reason
    ratio = (
        income.value / spending.value
        if available and spending.value > 0
        else Decimal("0")
    )
    included, excluded, uncertain = filter_insight_rows(
        dataset,
        confidence_policy=analysis.confidence_policy,
    )
    metadata = build_insight_metadata(
        dataset,
        included=included,
        excluded=excluded,
        uncertain=uncertain,
        available=available,
        unavailable_reason=unavailable_reason,
    )
    return CashFlowQualityResult(
        available=available,
        unavailable_reason=unavailable_reason,
        inflow_outflow_ratio=ratio,
        is_healthy=available and income.value > spending.value,
        analyzed_months=analysis.min_complete_months if available else 0,
        total_income=income.value if available else Decimal("0"),
        total_spending=spending.value if available else Decimal("0"),
        net_cash_flow=(income.value - spending.value) if available else Decimal("0"),
        metadata=metadata,
    )


def format_cash_flow_quality(result: CashFlowQualityResult, language: str = "en") -> str:
    if not result.available:
        if result.unavailable_reason == "insufficient_complete_months":
            return render_message("query.insight.cash_flow.insufficient_history", language)
        return render_message("query.insight.coverage_required", language)
    params = {
        "ratio": f"{result.inflow_outflow_ratio:.2f}",
        "income": format_naira_compact(result.total_income),
        "spending": format_naira_compact(result.total_spending),
        "net": format_naira_compact(result.net_cash_flow),
        "months": result.analyzed_months,
    }
    if result.is_healthy:
        return render_message("query.insight.cash_flow.healthy", language, params)
    return render_message("query.insight.cash_flow.unhealthy", language, params)


def build_cash_flow_quality_surface(result: CashFlowQualityResult, language: str = "en") -> SurfaceView:
    return SurfaceView(
        mode=SurfaceViewMode.INSIGHT,
        lead_text=format_cash_flow_quality(result, language),
        items=[],
    )


def build_cash_flow_quality_items(result: CashFlowQualityResult, language: str = "en") -> list[dict]:
    del result, language
    return []
