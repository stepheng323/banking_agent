"""Analytics and breakdown handlers."""

from collections import defaultdict
from datetime import date
from typing import Any

from apps.core.src.agent.sub_agents.query.fetch import (
    extract_counterparty,
    fetch_and_filter,
    parse_date,
)
from apps.core.src.agent.sub_agents.query.models import NormalizedQuery, QueryResult, QueryResultItem
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_analytics(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
) -> QueryResult:
    """Handle analytics summary queries."""
    transactions = await fetch_and_filter(provider, query, account_id, account_ids, accounts_info)

    if not query.aggregation:
        return QueryResult(summary_text="No aggregation specified.")

    agg_type = query.aggregation.type

    if agg_type == "sum":
        total = sum(t.get("amount", 0) for t in transactions) / 100
        return QueryResult(summary_text=f"Total: ₦{total:,.2f}")

    elif agg_type == "average":
        if transactions:
            avg = sum(t.get("amount", 0) for t in transactions) / len(transactions) / 100
            return QueryResult(summary_text=f"Average: ₦{avg:,.2f}")
        return QueryResult(summary_text="No transactions found.")

    elif agg_type == "count":
        return QueryResult(summary_text=f"Count: {len(transactions)} transactions")

    elif agg_type == "largest":
        limit = query.aggregation.limit or 5
        sorted_txns = sorted(transactions, key=lambda t: t.get("amount", 0), reverse=True)
        items = [
            QueryResultItem(
                id=t.get("id", "")[:8] if t.get("id") else str(i),
                description=t.get("narration", "Transaction"),
                amount=t.get("amount", 0) / 100,
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
            from apps.core.src.agent.sub_agents.query.models import detect_category

            key = detect_category(t.get("narration", "")) or "other"
        elif group_by == "merchant":
            key = extract_counterparty(t.get("narration", ""))
        else:
            key = t.get("date", "")[:10]

        tx_type = t.get("type", "unknown")
        if tx_type in ("debit", "credit"):
            grouped[key][tx_type] += t.get("amount", 0)
            grouped[key]["count"] += 1

    items = [
        QueryResultItem(
            id=str(i),
            description=key,
            amount=(data["debit"] + data["credit"]) / 100,
            date=parse_date(key) if group_by == "day" else date.today(),
            metadata={"debit": data["debit"] / 100, "credit": data["credit"] / 100, "count": data["count"]},
        )
        for i, (key, data) in enumerate(sorted(grouped.items(), reverse=True)[:10])
    ]

    return QueryResult(
        summary_text=f"Breakdown by {group_by}",
        items=items,
    )
