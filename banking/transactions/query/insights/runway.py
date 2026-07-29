"""Coverage-aware deterministic cash-runway estimate."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Literal

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.operations import AnalyzeOperation, RunwaySpec
from banking.transactions.query.services.analysis.kernel.contracts import RunwayResult
from banking.transactions.query.services.analysis.kernel.insight_support import (
    build_insight_metadata,
    filter_insight_rows,
    inclusive_window,
    insight_available,
)
from banking.transactions.query.services.analysis.kernel.metrics import row_amount, row_direction
from banking.transactions.query.services.analysis.kernel.service import AnalysisService

_MINIMUM_BASELINE_DAYS = 30


async def _authoritative_balance(service: AnalysisService, account_ids: list[str]) -> tuple[Decimal, bool]:
    results = await asyncio.gather(
        *(service.provider.get_balance(account_id, real_time=True) for account_id in account_ids),
        return_exceptions=True,
    )
    total = Decimal("0")
    for result in results:
        if isinstance(result, BaseException) or result is None:
            return Decimal("0"), False
        total += Decimal(str(result.available_balance))
    return total, True


async def execute_runway(
    service: AnalysisService,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None,
    *,
    user_id: str | None,
    language: str = "en",
) -> RunwayResult:
    del accounts_info
    operation = contract.operation
    if not isinstance(operation, AnalyzeOperation) or not isinstance(operation.analysis, RunwaySpec):
        raise ValueError("runway execution requires RunwaySpec")
    analysis = operation.analysis
    selected_account_ids = list(dict.fromkeys(account_ids or [account_id]))
    current_balance, balance_available = await _authoritative_balance(service, selected_account_ids)
    start, end = inclusive_window(end=operation.scope.period.end, days=analysis.baseline_days)
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
        selected_account_ids,
        None,
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
    if not balance_available:
        available, unavailable_reason = False, "balance_unavailable"
    elif observed_days < _MINIMUM_BASELINE_DAYS:
        available, unavailable_reason = False, "insufficient_history"
    total_spend = sum((row_amount(row) for row in debits), Decimal("0"))
    average_burn = total_spend / Decimal(observed_days) if available and observed_days else Decimal("0")
    status: Literal["unavailable", "depleted", "bounded", "unbounded"]
    if not available:
        status = "unavailable"
        runway_days = None
    elif current_balance <= 0:
        status = "depleted"
        runway_days = 0
    elif average_burn <= 0:
        status = "unbounded"
        runway_days = None
    else:
        status = "bounded"
        runway_days = int(current_balance / average_burn)
    metadata = build_insight_metadata(
        dataset,
        included=debits,
        excluded=excluded,
        uncertain=uncertain,
        available=available,
        unavailable_reason=unavailable_reason,
    )
    return RunwayResult(
        available=available,
        unavailable_reason=unavailable_reason,
        average_burn_rate=average_burn,
        current_balance=current_balance,
        runway_days=runway_days,
        status=status,
        metadata=metadata,
    )


def format_runway(result: RunwayResult, language: str = "en") -> str:
    if not result.available:
        if result.unavailable_reason == "balance_unavailable":
            return render_message("query.insight.runway.balance_unavailable", language)
        if result.unavailable_reason == "insufficient_history":
            return render_message("query.insight.runway.insufficient_history", language)
        return render_message("query.insight.runway.coverage_required", language)
    if result.status == "unbounded":
        return render_message(
            "query.insight.runway.unbounded",
            language,
            {"balance": format_naira_compact(result.current_balance)},
        )
    if result.status == "depleted":
        return render_message("query.insight.runway.depleted", language)
    return render_message(
        "query.insight.runway.summary",
        language,
        {
            "days": result.runway_days,
            "burn_rate": format_naira_compact(result.average_burn_rate),
            "balance": format_naira_compact(result.current_balance),
        },
    )


def build_runway_surface(result: RunwayResult, language: str = "en") -> SurfaceView:
    return SurfaceView(
        mode=SurfaceViewMode.INSIGHT,
        lead_text=format_runway(result, language),
        items=[],
    )


def build_runway_items(result: RunwayResult, language: str = "en") -> list[dict]:
    del result, language
    return []
