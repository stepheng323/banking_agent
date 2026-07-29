"""Coverage-aware deterministic operating-spend forecast."""

from __future__ import annotations

from decimal import Decimal

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation, ForecastSpec
from banking.transactions.query.services.analysis.kernel.contracts import ForecastResult
from banking.transactions.query.services.analysis.kernel.insight_support import (
    build_insight_metadata,
    filter_insight_rows,
    inclusive_window,
    insight_available,
)
from banking.transactions.query.services.analysis.kernel.metrics import row_amount, row_direction
from banking.transactions.query.services.analysis.kernel.service import AnalysisService

_MINIMUM_HISTORY_DAYS = 30


async def execute_forecast(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> ForecastResult:
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation) or not isinstance(operation.analysis, ForecastSpec):
        raise ValueError("forecast execution requires ForecastSpec")
    analysis = operation.analysis
    start, end = inclusive_window(end=operation.scope.period.end, days=analysis.history_days)
    modified_contract = contract.model_copy(
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
        modified_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        basis=analysis.analysis_basis,
        period_label="history",
    )
    eligible, excluded, uncertain = filter_insight_rows(
        dataset,
        confidence_policy=analysis.confidence_policy,
    )
    debits = [row for row in eligible if row_direction(row) == "debit"]
    observed_days = dataset.fully_covered_days or 0
    available, unavailable_reason = insight_available(
        dataset,
        completeness_policy=analysis.completeness_policy,
    )
    if observed_days < _MINIMUM_HISTORY_DAYS:
        available, unavailable_reason = False, "insufficient_history"
    total_spend = sum((row_amount(row) for row in debits), Decimal("0"))
    average_daily = total_spend / Decimal(observed_days) if available and observed_days else Decimal("0")
    projected = average_daily * Decimal(analysis.horizon_days)
    metadata = build_insight_metadata(
        dataset,
        included=debits,
        excluded=excluded,
        uncertain=uncertain,
        available=available,
        unavailable_reason=unavailable_reason,
    )
    return ForecastResult(
        available=available,
        unavailable_reason=unavailable_reason,
        average_daily_spend=average_daily,
        projected_spend=projected,
        horizon_days=analysis.horizon_days,
        observed_days=observed_days,
        metadata=metadata,
    )


def format_forecast(result: ForecastResult, language: str = "en") -> str:
    if not result.available:
        if result.unavailable_reason == "insufficient_history":
            return render_message("query.insight.forecast.insufficient_history", language)
        return render_message("query.insight.coverage_required", language)
    if result.projected_spend <= 0:
        return render_message("query.insight.forecast.no_spend", language)
    return render_message(
        "query.insight.forecast.summary",
        language,
        {
            "horizon": result.horizon_days,
            "projected": format_naira_compact(result.projected_spend),
            "daily": format_naira_compact(result.average_daily_spend),
            "observed_days": result.observed_days,
        },
    )


def build_forecast_surface(result: ForecastResult, language: str = "en") -> SurfaceView:
    return SurfaceView(
        mode=SurfaceViewMode.INSIGHT,
        lead_text=format_forecast(result, language),
        items=[],
    )


def build_forecast_items(result: ForecastResult, language: str = "en") -> list[dict]:
    del result, language
    return []
