"""Beneficiary summary handler."""

from collections import defaultdict
from datetime import date
from typing import Any

from apps.core.src.agent.sub_agents.query.fetch import extract_counterparty, fetch_and_filter
from apps.core.src.agent.sub_agents.query.models import NormalizedQuery, QueryResult, QueryResultItem
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_beneficiary_summary(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
) -> QueryResult:
    """Handle beneficiary summary queries."""
    transactions = await fetch_and_filter(provider, query, account_id, account_ids, accounts_info)

    debits = [t for t in transactions if t.get("type") == "debit"]

    counterparties: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "count": 0})
    for t in debits:
        name = extract_counterparty(t.get("narration", ""))
        counterparties[name]["total"] += t.get("amount", 0)
        counterparties[name]["count"] += 1

    sorted_cp = sorted(counterparties.items(), key=lambda x: x[1]["total"], reverse=True)
    limit = query.aggregation.limit if query.aggregation else 5

    items = [
        QueryResultItem(
            id=str(i),
            description=name,
            amount=data["total"] / 100,
            date=query.time_range.end if query.time_range else date.today(),
            metadata={"count": data["count"]},
        )
        for i, (name, data) in enumerate(sorted_cp[:limit])
    ]

    return QueryResult(
        summary_text=f"Top {len(items)} recipients",
        items=items,
    )
