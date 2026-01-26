"""Transaction list and search handlers."""

from apps.core.src.agent.graphs.query.models import (
    NormalizedQuery,
    QueryResult,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
)
from apps.core.src.agent.graphs.query.services.fetch import fetch_and_filter, parse_date
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_transaction_list(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
) -> QueryResult:
    """Handle transaction list queries."""
    transactions = await fetch_and_filter(provider, query, account_id, account_ids, accounts_info, user_id=user_id)

    # Apply result_limit if specified (e.g., "last transaction" → 1)
    if query.result_limit:
        transactions = transactions[: query.result_limit]

    offset = current_page * page_size
    paginated = transactions[offset : offset + page_size]

    items = [
        QueryResultItem(
            id=t.get("id", "")[:8] if t.get("id") else str(i),
            description=t.get("narration", "Transaction"),
            amount=abs(t.get("amount", 0)),  # Provider already returns Naira
            date=parse_date(t.get("date", "")),
            metadata={
                "type": t.get("type"),
                "bank_name": t.get("bank_name", ""),
                "transaction_type": t.get("transaction_type"),
                "status": t.get("status", ""),
            },
        )
        for i, t in enumerate(paginated)
    ]

    total = len(transactions)
    showing_end = offset + len(paginated)
    account_count = len(account_ids) if account_ids else 1

    result_summary = f"accounts:{account_count}|showing:{offset + 1}-{showing_end}|total:{total}"
    has_more = showing_end < total

    # Construct ResultSurface for interactive session

    surface = None
    if transactions:
        surface_items = [
            {
                "id": item.id,
                "key": item.description,
                "amount": item.amount,
                "count": 1,
            }
            for item in items
        ]

        surface = ResultSurface(
            type=SurfaceType.LIST,
            items=surface_items,
            context={
                "count": len(items),
                "total_results": total,
                "has_more": has_more,
            },
        )

    return QueryResult(
        summary_text=result_summary,
        items=items,
        has_more=has_more,
        surface=surface,
    )


async def handle_transaction_search(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
) -> QueryResult:
    """Handle transaction search (same as list but with merchant filter)."""
    return await handle_transaction_list(
        provider,
        query,
        account_id,
        account_ids,
        accounts_info,
        current_page,
        page_size,
        user_id=user_id,
    )
