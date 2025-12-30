"""Transaction list and search handlers."""

from apps.core.src.agent.sub_agents.query.fetch import fetch_and_filter, parse_date
from apps.core.src.agent.sub_agents.query.models import NormalizedQuery, QueryResult, QueryResultItem
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_transaction_list(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
) -> QueryResult:
    """Handle transaction list queries."""
    transactions = await fetch_and_filter(provider, query, account_id, account_ids)
    limit = query.aggregation.limit if query.aggregation else 10

    items = [
        QueryResultItem(
            id=t.get("id", "")[:8] if t.get("id") else str(i),
            description=t.get("narration", "Transaction"),
            amount=t.get("amount", 0) / 100,
            date=parse_date(t.get("date", "")),
            metadata={"type": t.get("type")},
        )
        for i, t in enumerate(transactions[:limit])
    ]

    return QueryResult(
        summary_text=f"Found {len(transactions)} transactions",
        items=items,
        has_more=len(transactions) > limit,
    )


async def handle_transaction_search(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
) -> QueryResult:
    """Handle transaction search (same as list but with merchant filter)."""
    return await handle_transaction_list(provider, query, account_id, account_ids)
