"""Cash flow handler."""

from typing import Literal

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import (
    CashFlowSummaryResult,
    QueryRequest,
    QueryResult,
)
from banking.transactions.query.models.operations import CashFlowSummarySpec, SummarizeOperation
from banking.transactions.query.presentation.time_format import build_timeframe_suffix
from banking.transactions.query.services.analysis.kernel.contracts import AnalysisDataset, CoverageStatus
from banking.transactions.query.services.analysis.kernel.service import AnalysisService
from banking.transactions.query.services.fetching.fetch import fetch_transactions_base
from banking.transactions.query.utils.totals import calculate_financial_totals
from shared.clients.abstractions.banking import BankDataProvider


async def handle_cash_flow(
    provider: BankDataProvider,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
    language: str = "en",
) -> QueryResult:
    """Handle cash flow summary queries."""
    del current_page, page_size
    transactions = await fetch_transactions_base(
        provider,
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
    )

    time_range = contract.time_range
    start_date_str = time_range.start.isoformat() if time_range and time_range.start else "0000-00-00"
    end_date_str = time_range.end.isoformat() if time_range and time_range.end else "9999-99-99"

    in_range_txns = []
    for t in transactions:
        tx_date = str(t.get("date", ""))[:10]
        if start_date_str <= tx_date <= end_date_str:
            in_range_txns.append(t)

    analysis_service = AnalysisService(provider)
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="selected",
        start_date=contract.time_start,
        end_date=contract.time_end,
        rows=in_range_txns,
        coverage_status=CoverageStatus.UNAVAILABLE,
    )
    analysis = await analysis_service.analyze_cash_flow(dataset)
    display_totals = calculate_financial_totals(analysis.settled_rows, account_id)
    total_inflow = analysis.inflow.value
    total_outflow = analysis.outflow.value
    net_flow = analysis.net_cash_flow.value

    status_label: Literal["positive", "negative", "neutral"] = "neutral"
    if net_flow > 0:
        status_label = "positive"
    elif net_flow < 0:
        status_label = "negative"

    cash_flow_result = CashFlowSummaryResult(
        period_label="Selected Period",
        currency="NGN",
        total_inflow=int(total_inflow),
        total_outflow=int(total_outflow),
        net_flow=int(net_flow),
        inflow_count=analysis.inflow.count,
        outflow_count=analysis.outflow.count,
        account_scope=contract.accounts_scope,
        account_breakdown=(
            list(display_totals.account_breakdowns.values()) if contract.accounts_scope == "all" else None
        ),
        excluded_internal_transfers_count=analysis.excluded_internal_count,
        excluded_reversals_count=analysis.excluded_unsettled_count,
        status=status_label,
    )

    operation = contract.operation
    group_by = (
        operation.summary.group_by
        if isinstance(operation, SummarizeOperation) and isinstance(operation.summary, CashFlowSummarySpec)
        else None
    )
    if group_by == "account" and display_totals.account_breakdowns:
        lines = ["Cash flow by account"]
        for breakdown in sorted(
            display_totals.account_breakdowns.values(), key=lambda item: abs(item.net_flow), reverse=True
        ):
            suffix = f" · ···{breakdown.masked_account_number}" if breakdown.masked_account_number else ""
            if breakdown.net_flow > 0:
                net_text = f"up ₦{breakdown.net_flow:,.0f}"
            elif breakdown.net_flow < 0:
                net_text = f"down ₦{abs(breakdown.net_flow):,.0f}"
            else:
                net_text = "balanced"
            lines.extend(
                [
                    "",
                    f"{breakdown.bank_name}{suffix}",
                    f"₦{breakdown.total_inflow:,.0f} came in · ₦{breakdown.total_outflow:,.0f} went out · {net_text}",
                ]
            )
        summary_text = "\n".join(lines)
    elif total_inflow == 0 and total_outflow == 0:
        direction = contract.filters.transaction_type if contract.filters is not None else None
        timeframe = build_timeframe_suffix(contract, language)
        if direction == "credit":
            summary_text = render_message(
                "query.analytics.no_income",
                language,
                {"target_description": "", "timeframe": timeframe},
            )
        elif direction == "debit":
            summary_text = render_message(
                "query.analytics.no_spending",
                language,
                {"target_description": "", "timeframe": timeframe},
            )
        else:
            summary_text = render_message("query.format.no_matching_transactions", language)
    elif net_flow > 0:
        summary_text = f"₦{total_inflow:,.0f} came in and ₦{total_outflow:,.0f} went out. You're up ₦{net_flow:,.0f}."
    elif net_flow < 0:
        summary_text = (
            f"₦{total_inflow:,.0f} came in and ₦{total_outflow:,.0f} went out. You're down ₦{abs(net_flow):,.0f}."
        )
    else:
        summary_text = (
            f"₦{total_inflow:,.0f} came in and ₦{total_outflow:,.0f} went out. Your cash flow is perfectly balanced."
        )

    return QueryResult(
        summary_text=summary_text,
        cash_flow=cash_flow_result,
        items=[],
        has_more=False,
    )
