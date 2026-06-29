"""Time comparison query handler.

Compares spending/transactions between two time periods.
Example: "How did my spending this month compare to last month?"
"""

from calendar import monthrange
from datetime import timedelta
from typing import Any

from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import (
    ComparisonDirective,
    QueryExecutionContract,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.services.fetching.fetch import fetch_and_filter
from banking.transactions.query.utils.totals import calculate_financial_totals
from shared.clients.abstractions.banking import BankDataProvider


async def handle_time_comparison(
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
    """Handle time comparison queries (this month vs last month, etc.)."""
    del current_page, page_size
    if not contract.time_range:
        return QueryResult(summary_text=render_message("query.time_comparison.prompt_specify_period", language))

    # Get current period data
    current_period = contract.time_range
    comparison_period = _get_comparison_period(current_period, directive=contract.comparison)

    comparison_contract = contract.model_copy(deep=True)
    comparison_contract.time_start = comparison_period.start
    comparison_contract.time_end = comparison_period.end
    if comparison_contract.execution_plan is not None:
        comparison_contract.execution_plan = comparison_contract.execution_plan.model_copy(
            update={"time_range": comparison_period}
        )

    # Fetch transactions for both periods
    current_txns = await fetch_and_filter(
        provider,
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
    )
    comparison_txns = await fetch_and_filter(
        provider,
        comparison_contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
    )

    # Calculate totals
    current_stats = _calculate_stats(current_txns)
    comparison_stats = _calculate_stats(comparison_txns)

    # Build response
    current_label = _format_period_label(current_period)
    comparison_label = _format_period_label(comparison_period)

    items = []

    # Spending comparison (debits)
    if current_stats["debit_total"] > 0 or comparison_stats["debit_total"] > 0:
        change = current_stats["debit_total"] - comparison_stats["debit_total"]
        pct_change = _calculate_percentage_change(comparison_stats["debit_total"], current_stats["debit_total"])

        items.append(
            QueryResultItem(
                id="spending",
                description=render_message(
                    "query.time_comparison.item_spending",
                    language,
                    {"change": _format_change(change, pct_change, language)},
                ),
                amount=current_stats["debit_total"],
                date=current_period.end,
                metadata={
                    "current": current_stats["debit_total"],
                    "comparison": comparison_stats["debit_total"],
                    "change": change,
                    "pct_change": pct_change,
                },
            )
        )

    # Income comparison (credits)
    if current_stats["credit_total"] > 0 or comparison_stats["credit_total"] > 0:
        change = current_stats["credit_total"] - comparison_stats["credit_total"]
        pct_change = _calculate_percentage_change(comparison_stats["credit_total"], current_stats["credit_total"])

        items.append(
            QueryResultItem(
                id="income",
                description=render_message(
                    "query.time_comparison.item_income",
                    language,
                    {"change": _format_change(change, pct_change, language)},
                ),
                amount=current_stats["credit_total"],
                date=current_period.end,
                metadata={
                    "current": current_stats["credit_total"],
                    "comparison": comparison_stats["credit_total"],
                    "change": change,
                    "pct_change": pct_change,
                },
            )
        )

    # Transaction count comparison
    items.append(
        QueryResultItem(
            id="count",
            description=render_message(
                "query.time_comparison.item_transactions",
                language,
                {"current": current_stats["count"], "comparison": comparison_stats["count"]},
            ),
            amount=float(current_stats["count"]),
            date=current_period.end,
            metadata={
                "current": current_stats["count"],
                "comparison": comparison_stats["count"],
            },
        )
    )

    summary = _build_summary(current_label, comparison_label, current_stats, comparison_stats, language)

    return QueryResult(
        summary_text=summary,
        items=items,
    )


def _safe_shift_year(day: TimeRange, years_back: int = 1) -> TimeRange:
    """Shift a range backwards by years with leap-day safety."""
    try:
        return TimeRange(
            start=day.start.replace(year=day.start.year - years_back),
            end=day.end.replace(year=day.end.year - years_back),
            granularity=day.granularity,
        )
    except ValueError:
        # Handle leap-day and similar edge-cases by clamping to previous day.
        return TimeRange(
            start=(day.start - timedelta(days=1)).replace(year=day.start.year - years_back),
            end=(day.end - timedelta(days=1)).replace(year=day.end.year - years_back),
            granularity=day.granularity,
        )


def _get_comparison_period(current: TimeRange, directive: ComparisonDirective | None = None) -> TimeRange:
    """Calculate comparison period from the contract directive."""
    if directive and directive.mode == "explicit_range" and directive.explicit_range:
        return directive.explicit_range
    if directive and directive.mode == "year_ago":
        return _safe_shift_year(current, years_back=1)

    # Default: same duration, immediately previous timeframe.
    duration = (current.end - current.start).days + 1

    # Shift back by the same duration
    comparison_end = current.start - timedelta(days=1)
    comparison_start = comparison_end - timedelta(days=duration - 1)

    return TimeRange(
        start=comparison_start,
        end=comparison_end,
        granularity=current.granularity,
    )


def _calculate_stats(transactions: list[dict]) -> dict[str, Any]:
    """Calculate statistics from transactions."""
    totals = calculate_financial_totals(transactions)
    return {
        "debit_total": float(totals.total_outflow),
        "credit_total": float(totals.total_inflow),
        "count": len(totals.settled_transactions),
        "net": float(totals.net_flow),
    }


def _calculate_percentage_change(old_value: float, new_value: float) -> float | None:
    """Calculate percentage change."""
    if old_value == 0:
        return None
    return ((new_value - old_value) / old_value) * 100


def _format_change(change: float, pct_change: float | None, locale: str = "en") -> str:
    """Format change for display."""
    direction = "↑" if change > 0 else "↓" if change < 0 else "→"
    abs_change = abs(change)
    amount = format_naira(abs_change)

    if pct_change is not None:
        return f"{direction} {amount} ({abs(pct_change):.0f}%)"
    if change != 0:
        return f"{direction} {amount}"
    return render_message("query.time_comparison.no_change", locale)


def _format_period_label(period: TimeRange) -> str:
    """Format a time range as a readable label."""
    is_same_month = period.start.month == period.end.month and period.start.year == period.end.year
    if is_same_month and _is_full_month_window(period):
        return period.start.strftime("%B %Y")
    if period.start.year == period.end.year:
        return f"{period.start.strftime('%b %d')} - {period.end.strftime('%b %d')}"
    return f"{period.start.strftime('%b %d, %Y')} - {period.end.strftime('%b %d, %Y')}"


def _is_full_month_window(period: TimeRange) -> bool:
    """Return True when range spans a complete calendar month."""
    if period.start.year != period.end.year or period.start.month != period.end.month:
        return False
    expected_last_day = monthrange(period.start.year, period.start.month)[1]
    return period.start.day == 1 and period.end.day == expected_last_day


def _build_summary(
    current_label: str,
    comparison_label: str,
    current_stats: dict[str, Any],
    comparison_stats: dict[str, Any],
    locale: str = "en",
) -> str:
    """Build the summary text."""
    spending_change = current_stats["debit_total"] - comparison_stats["debit_total"]

    if spending_change > 0:
        return render_message(
            "query.time_comparison.summary_spent_more",
            locale,
            {
                "current_label": current_label,
                "comparison_label": comparison_label,
                "current_amount": f"{current_stats['debit_total']:,.0f}",
                "comparison_amount": f"{comparison_stats['debit_total']:,.0f}",
                "amount": f"{spending_change:,.0f}",
            },
        )
    elif spending_change < 0:
        return render_message(
            "query.time_comparison.summary_spent_less",
            locale,
            {
                "current_label": current_label,
                "comparison_label": comparison_label,
                "current_amount": f"{current_stats['debit_total']:,.0f}",
                "comparison_amount": f"{comparison_stats['debit_total']:,.0f}",
                "amount": f"{abs(spending_change):,.0f}",
            },
        )
    else:
        return render_message(
            "query.time_comparison.same_spending",
            locale,
            {"current_label": current_label, "comparison_label": comparison_label},
        )
