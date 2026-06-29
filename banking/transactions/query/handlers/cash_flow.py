"""Cash flow handler."""

from typing import Literal

from banking.transactions.query.models.domain import (
    CashFlowSummaryResult,
    QueryExecutionContract,
    QueryResult,
)
from banking.transactions.query.services.fetching.fetch import fetch_transactions_base
from banking.transactions.query.utils.totals import calculate_financial_totals
from shared.clients.abstractions.banking import BankDataProvider


async def handle_cash_flow(
    provider: BankDataProvider,
    contract: QueryExecutionContract,
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

    totals = calculate_financial_totals(in_range_txns, account_id)

    status_label: Literal["positive", "negative", "neutral"] = "neutral"
    if totals.net_flow > 0:
        status_label = "positive"
    elif totals.net_flow < 0:
        status_label = "negative"

    cash_flow_result = CashFlowSummaryResult(
        period_label="Selected Period",
        currency="NGN",
        total_inflow=totals.total_inflow,
        total_outflow=totals.total_outflow,
        net_flow=totals.net_flow,
        inflow_count=totals.inflow_count,
        outflow_count=totals.outflow_count,
        account_scope=contract.accounts_scope,
        account_breakdown=list(totals.account_breakdowns.values()) if contract.accounts_scope == "all" else None,
        excluded_internal_transfers_count=totals.excluded_internal_count,
        excluded_reversals_count=totals.excluded_reversals_count,
        status=status_label,
    )

    group_by = contract.aggregation.group_by if contract.aggregation else None
    if group_by == "account" and totals.account_breakdowns:
        lines = ["Cash flow by account"]
        for breakdown in sorted(totals.account_breakdowns.values(), key=lambda item: abs(item.net_flow), reverse=True):
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
    elif totals.total_inflow == 0 and totals.total_outflow == 0:
        summary_text = "No cash flow activity found for this period."
    elif totals.net_flow > 0:
        summary_text = (
            f"₦{totals.total_inflow:,.0f} came in and ₦{totals.total_outflow:,.0f} went out. "
            f"You're up ₦{totals.net_flow:,.0f}."
        )
    elif totals.net_flow < 0:
        summary_text = (
            f"₦{totals.total_inflow:,.0f} came in and ₦{totals.total_outflow:,.0f} went out. "
            f"You're down ₦{abs(totals.net_flow):,.0f}."
        )
    else:
        summary_text = (
            f"₦{totals.total_inflow:,.0f} came in and ₦{totals.total_outflow:,.0f} went out. "
            "Your cash flow is perfectly balanced."
        )

    return QueryResult(
        summary_text=summary_text,
        cash_flow=cash_flow_result,
        items=[],
        has_more=False,
    )
