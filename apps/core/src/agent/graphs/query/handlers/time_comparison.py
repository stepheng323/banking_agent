"""Time comparison query handler.

Compares spending/transactions between two time periods.
Example: "How did my spending this month compare to last month?"
"""

from datetime import timedelta
from typing import Any

from apps.core.src.agent.graphs.query.fetch import fetch_and_filter
from apps.core.src.agent.graphs.query.models import (
    NormalizedQuery,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_time_comparison(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
) -> QueryResult:
    """Handle time comparison queries (this month vs last month, etc.)."""
    if not query.time_range:
        return QueryResult(summary_text="Please specify a time period to compare.")

    # Get current period data
    current_period = query.time_range
    comparison_period = _get_comparison_period(current_period)

    # Create a modified query for the comparison period
    comparison_query = query.model_copy(update={"time_range": comparison_period})

    # Fetch transactions for both periods
    current_txns = await fetch_and_filter(provider, query, account_id, account_ids, accounts_info)
    comparison_txns = await fetch_and_filter(provider, comparison_query, account_id, account_ids, accounts_info)

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
                description=f"Spending: {_format_change(change, pct_change)}",
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
                description=f"Income: {_format_change(change, pct_change)}",
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
            description=f"Transactions: {current_stats['count']} vs {comparison_stats['count']}",
            amount=float(current_stats["count"]),
            date=current_period.end,
            metadata={
                "current": current_stats["count"],
                "comparison": comparison_stats["count"],
            },
        )
    )

    summary = _build_summary(current_label, comparison_label, current_stats, comparison_stats)

    return QueryResult(
        summary_text=summary,
        items=items,
    )


def _get_comparison_period(current: TimeRange) -> TimeRange:
    """Calculate the comparison period (same duration, earlier timeframe)."""
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
    debit_total = 0.0
    credit_total = 0.0
    count = 0

    for t in transactions:
        amount = t.get("amount", 0) / 100  # Convert from kobo
        tx_type = t.get("type", "")
        count += 1

        if tx_type == "debit":
            debit_total += amount
        elif tx_type == "credit":
            credit_total += amount

    return {
        "debit_total": debit_total,
        "credit_total": credit_total,
        "count": count,
        "net": credit_total - debit_total,
    }


def _calculate_percentage_change(old_value: float, new_value: float) -> float | None:
    """Calculate percentage change."""
    if old_value == 0:
        return None
    return ((new_value - old_value) / old_value) * 100


def _format_change(change: float, pct_change: float | None) -> str:
    """Format change for display."""
    direction = "↑" if change > 0 else "↓" if change < 0 else "→"
    abs_change = abs(change)

    if pct_change is not None:
        return f"{direction} ₦{abs_change:,.0f} ({abs(pct_change):.0f}%)"
    elif change != 0:
        return f"{direction} ₦{abs_change:,.0f}"
    else:
        return "No change"


def _format_period_label(period: TimeRange) -> str:
    """Format a time range as a readable label."""
    if period.start.month == period.end.month and period.start.year == period.end.year:
        return period.start.strftime("%B %Y")
    return f"{period.start.strftime('%b %d')} - {period.end.strftime('%b %d')}"


def _build_summary(
    current_label: str,
    comparison_label: str,
    current_stats: dict[str, Any],
    comparison_stats: dict[str, Any],
) -> str:
    """Build the summary text."""
    spending_change = current_stats["debit_total"] - comparison_stats["debit_total"]

    if spending_change > 0:
        verb = "spent more"
        amount = spending_change
    elif spending_change < 0:
        verb = "spent less"
        amount = abs(spending_change)
    else:
        return f"Your spending in {current_label} is the same as {comparison_label}."

    return f"You {verb} (₦{amount:,.0f}) in {current_label} compared to {comparison_label}."
