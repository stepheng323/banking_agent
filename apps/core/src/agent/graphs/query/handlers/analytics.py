"""Analytics and breakdown handlers."""

from collections import defaultdict
from datetime import date
from typing import Any

from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResult, QueryResultItem
from apps.core.src.agent.graphs.query.services.fetch import (
    extract_counterparty,
    fetch_and_filter,
    parse_date,
)
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_analytics(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
) -> QueryResult:
    """Handle analytics summary queries."""
    transactions = await fetch_and_filter(provider, query, account_id, account_ids, accounts_info, user_id=user_id)

    if not query.aggregation:
        return QueryResult(summary_text="No aggregation specified.")

    agg_type = query.aggregation.type

    if agg_type == "sum":
        total = sum(abs(t.get("amount", 0)) for t in transactions)
        count = len(transactions)
        if count == 0:
            return QueryResult(summary_text="No matching transactions found.")
        merchant = query.filters.merchant[0] if query.filters and query.filters.merchant else "your search"

        timeframe = " (last 30 days)"
        if query.time_range:
            start_str = query.time_range.start.strftime("%b %d")
            end_str = query.time_range.end.strftime("%b %d")
            timeframe = f" ({start_str} - {end_str})"

        items = [
            QueryResultItem(
                id=t.get("id", "")[:8] if t.get("id") else str(i),
                description=t.get("narration", "Transaction"),
                amount=abs(t.get("amount", 0)),
                date=parse_date(t.get("date", "")),
                metadata={
                    "bank_name": t.get("bank_name", ""),
                    "type": t.get("type", ""),
                    "counterparty": t.get("counterparty"),
                },
            )
            for i, t in enumerate(transactions)
        ]

        return QueryResult(
            summary_text=f"💸 You spent *₦{total:,.2f}* on {merchant}{timeframe} ({count} transaction{'s' if count > 1 else ''}).\n\n_'show transactions' to see details_",
            items=items,
            total_count=count,
        )

    elif agg_type == "average":
        if transactions:
            avg = sum(abs(t.get("amount", 0)) for t in transactions) / len(transactions)
            count = len(transactions)

            timeframe = " (last 30 days)"
            if query.time_range:
                start_str = query.time_range.start.strftime("%b %d")
                end_str = query.time_range.end.strftime("%b %d")
                timeframe = f" ({start_str} - {end_str})"

            items = [
                QueryResultItem(
                    id=t.get("id", "")[:8] if t.get("id") else str(i),
                    description=t.get("narration", "Transaction"),
                    amount=abs(t.get("amount", 0)),
                    date=parse_date(t.get("date", "")),
                    metadata={
                        "bank_name": t.get("bank_name", ""),
                        "type": t.get("type", ""),
                        "counterparty": t.get("counterparty"),
                    },
                )
                for i, t in enumerate(transactions)
            ]

            return QueryResult(
                summary_text=f"Your average transaction is *₦{int(avg):,}*{timeframe} ({count} transaction{'s' if count > 1 else ''}).\n\n_'show transactions' to see details_",
                items=items,
                total_count=count,
            )
        return QueryResult(summary_text="No transactions found.")

    elif agg_type == "count":
        count = len(transactions)
        return QueryResult(summary_text=f"You made *{count}* transaction{'s' if count != 1 else ''}{timeframe}.")

    elif agg_type == "largest":
        limit = query.aggregation.limit or 5
        sorted_txns = sorted(transactions, key=lambda t: t.get("amount", 0), reverse=True)
        items = [
            QueryResultItem(
                id=t.get("id", "")[:8] if t.get("id") else str(i),
                description=t.get("narration", "Transaction"),
                amount=t.get("amount", 0),
                date=parse_date(t.get("date", "")),
            )
            for i, t in enumerate(sorted_txns[:limit])
        ]
        return QueryResult(
            summary_text=f"Top {limit} largest transactions",
            items=items,
        )

    elif agg_type == "breakdown":
        return await _aggregate_breakdown(transactions, query)

    return QueryResult(summary_text="Aggregation completed.")


async def _aggregate_breakdown(transactions: list[dict], query: NormalizedQuery) -> QueryResult:
    """Aggregate transactions by day/category/merchant."""
    group_by = query.aggregation.group_by if query.aggregation else "day"
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"debit": 0, "credit": 0, "count": 0})

    for t in transactions:
        if group_by == "day":
            key = t.get("date", "")[:10]
        elif group_by == "category":
            from apps.core.src.agent.graphs.query.models import detect_category

            key = detect_category(t.get("narration", "")) or "other"
        elif group_by == "merchant":
            key = extract_counterparty(t.get("narration", ""))
        else:
            key = t.get("date", "")[:10]

        tx_type = t.get("type", "unknown")
        if tx_type in ("debit", "credit"):
            grouped[key][tx_type] += t.get("amount", 0)
            grouped[key]["count"] += 1

    # Sort: Amount (desc) -> Count (desc)
    # Special case: "Other" always goes to the bottom
    def sort_key(item):
        key, data = item
        if key.lower() == "other":
            return (-1.0, 0)
        return (data["debit"] + data["credit"], data["count"])

    sorted_items = sorted(grouped.items(), key=sort_key, reverse=True)

    # Apply limit if requested
    limit = query.aggregation.limit or 10
    sorted_items = sorted_items[:limit]

    items = [
        QueryResultItem(
            id=str(i),
            description=key,
            amount=data["debit"] + data["credit"],
            date=parse_date(key) if group_by == "day" else date.today(),
            metadata={
                "debit": data["debit"],
                "credit": data["credit"],
                "count": data["count"],
                "key": key,  # Original key for drill-down
            },
        )
        for i, (key, data) in enumerate(sorted_items)
    ]

    # Construct Surface for interactive session
    from apps.core.src.agent.graphs.query.models import ResultSurface, SurfaceType

    surface_items = [
        {
            "id": item.id,
            "key": item.description,
            "amount": item.amount,
            "count": item.metadata.get("count", 0),
        }
        for item in items
    ]

    surface = ResultSurface(
        type=SurfaceType.BREAKDOWN,
        items=surface_items,
        context={
            "group_by": group_by,
            "time_range": query.time_range.model_dump() if query.time_range else None,
        },
    )

    return QueryResult(
        summary_text=f"Breakdown by {group_by}",
        items=items,
        surface=surface,
    )
