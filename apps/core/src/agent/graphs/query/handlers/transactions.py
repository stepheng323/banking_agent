"""Transaction list and search handlers."""

import time
from typing import Any, cast

from apps.core.src.agent.graphs.query.models import (
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
)
from apps.core.src.agent.graphs.query.services.fetch import (
    apply_filters,
    build_cache_fingerprint,
    fetch_transactions_base,
    parse_date,
)
from shared.clients.abstractions.banking import BankDataProvider
from shared.i18n import render_message

TRANSACTION_CACHE_MAX_AGE_SECONDS = 90.0


def _coerce_session_cache(session_cache: dict[str, Any] | None) -> tuple[list[dict[str, Any]] | None, float | None, str | None]:
    if not isinstance(session_cache, dict):
        return None, None, None
    cached_transactions_raw = session_cache.get("cached_transactions")
    cached_transactions = (
        [cast(dict[str, Any], item) for item in cached_transactions_raw if isinstance(item, dict)]
        if isinstance(cached_transactions_raw, list)
        else None
    )
    fetched_at_raw = session_cache.get("cache_fetched_at")
    fetched_at = float(fetched_at_raw) if isinstance(fetched_at_raw, (int, float)) else None
    fingerprint = str(session_cache.get("cache_fingerprint") or "")
    return cached_transactions, fetched_at, (fingerprint or None)


async def handle_transaction_list(
    provider: BankDataProvider,
    contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
    language: str = "en",
    continuation_type: str | None = None,
    continuation_delta_type: str | None = None,
    session_cache: dict[str, Any] | None = None,
) -> QueryResult:
    """Handle transaction list queries."""
    query = contract.normalized_query
    cache_fingerprint = build_cache_fingerprint(query, account_id, account_ids, user_id=user_id)
    cached_transactions, cache_fetched_at, cached_fingerprint = _coerce_session_cache(session_cache)
    cache_age_seconds = (time.time() - cache_fetched_at) if cache_fetched_at is not None else None
    can_reuse_cache = (
        continuation_type == "filter_delta"
        and continuation_delta_type != "time"
        and cached_transactions is not None
        and cache_age_seconds is not None
        and cache_age_seconds <= TRANSACTION_CACHE_MAX_AGE_SECONDS
        and cached_fingerprint == cache_fingerprint
    )

    if can_reuse_cache and cached_transactions is not None:
        base_transactions: list[dict[str, Any]] = cached_transactions
    else:
        base_transactions = await fetch_transactions_base(
            provider,
            query,
            account_id,
            account_ids,
            accounts_info,
            user_id=user_id,
            language=language,
        )
    cache_fetched_at_value = cache_fetched_at if can_reuse_cache and cache_fetched_at is not None else time.time()
    transactions = apply_filters(base_transactions, query.filters) if query.filters else list(base_transactions)

    # Apply result_limit if specified (e.g., "last transaction" → 1)
    if query.result_limit:
        transactions = transactions[: query.result_limit]

    offset = current_page * page_size
    paginated = transactions[offset : offset + page_size]

    items = [
        QueryResultItem(
            id=t.get("id", "")[:8] if t.get("id") else str(i),
            description=t.get("narration", render_message("query.format.narration.transaction", language)),
            amount=abs(t.get("amount", 0)),  # Provider already returns Naira
            date=parse_date(t.get("date", "")),
            metadata={
                "type": t.get("type"),
                "bank_name": t.get("bank_name", ""),
                "transaction_type": t.get("transaction_type"),
                "status": t.get("status", ""),
                "transaction_id": t.get("transaction_id") or t.get("id"),
                "recipient_name": t.get("recipient_name"),
                "recipient_account": t.get("recipient_account"),
                "recipient_account_number": t.get("recipient_account_number"),
                "recipient_bank_name": t.get("recipient_bank_name"),
                "recipient_bank_code": t.get("recipient_bank_code"),
            },
        )
        for i, t in enumerate(paginated)
    ]

    total = len(transactions)
    showing_end = offset + len(paginated)
    account_count = len(account_ids) if account_ids else 1

    result_summary = f"accounts:{account_count}|showing:{offset + 1}-{showing_end}|total:{total}"
    has_more = showing_end < total

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

        if total == 1 and (query.result_limit == 1 or query.intent == QueryIntent.TRANSACTION_SEARCH):
            surface = ResultSurface(
                type=SurfaceType.SINGLE_ITEM,
                items=surface_items,
                context={"type": "single_transaction"},
            )
        else:
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
        cached_transactions=base_transactions,
        cache_fetched_at=cache_fetched_at_value,
        cache_fingerprint=cache_fingerprint,
        cache_reused=can_reuse_cache,
    )


async def handle_transaction_search(
    provider: BankDataProvider,
    contract: QueryExecutionContract,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
    language: str = "en",
    continuation_type: str | None = None,
    continuation_delta_type: str | None = None,
    session_cache: dict[str, Any] | None = None,
) -> QueryResult:
    """Handle transaction search (same as list but with merchant filter)."""
    return await handle_transaction_list(
        provider,
        contract,
        account_id,
        account_ids,
        accounts_info,
        current_page,
        page_size,
        user_id=user_id,
        language=language,
        continuation_type=continuation_type,
        continuation_delta_type=continuation_delta_type,
        session_cache=session_cache,
    )
