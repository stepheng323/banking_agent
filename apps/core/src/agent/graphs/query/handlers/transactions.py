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
    apply_time_window,
    build_cache_fingerprint,
    build_cache_scope_fingerprint,
    decide_transaction_cache_reuse,
    fetch_transactions_base,
    parse_date,
)
from shared.clients.abstractions.banking import BankDataProvider
from shared.i18n import render_message
from shared.utils.logging import get_logger

TRANSACTION_CACHE_MAX_AGE_SECONDS = 90.0
logger = get_logger(__name__)


def _transaction_sort_key(transaction: dict[str, Any]) -> tuple[str, str]:
    return (str(transaction.get("date", "")), str(transaction.get("id", "")))


def _coerce_session_cache(
    session_cache: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]] | None, float | None, str | None, str | None, str | None, str | None]:
    if not isinstance(session_cache, dict):
        return None, None, None, None, None, None
    cached_transactions_raw = session_cache.get("cached_transactions")
    cached_transactions = (
        [cast(dict[str, Any], item) for item in cached_transactions_raw if isinstance(item, dict)]
        if isinstance(cached_transactions_raw, list)
        else None
    )
    fetched_at_raw = session_cache.get("cache_fetched_at")
    fetched_at = float(fetched_at_raw) if isinstance(fetched_at_raw, (int, float)) else None
    fingerprint = str(session_cache.get("cache_fingerprint") or "")
    scope_fingerprint = str(session_cache.get("cache_scope_fingerprint") or "")
    cache_window_start = str(session_cache.get("cache_window_start") or "")
    cache_window_end = str(session_cache.get("cache_window_end") or "")
    return (
        cached_transactions,
        fetched_at,
        (fingerprint or None),
        (scope_fingerprint or None),
        (cache_window_start or None),
        (cache_window_end or None),
    )


def _log_query_trace(
    *,
    trace_context: dict[str, Any] | None,
    latency_ms: float,
    cache_reused: bool,
    cache_strategy: str,
    fetch_account_count: int,
) -> None:
    context = trace_context or {}
    logger.info(
        "query_trace",
        turn_id=context.get("turn_id"),
        inbound_message_id=context.get("inbound_message_id"),
        query_phase="transaction_handler",
        latency_ms=round(latency_ms, 2),
        cache_reused=cache_reused,
        cache_strategy=cache_strategy,
        fetch_account_count=fetch_account_count,
        outcome="ok",
    )


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
    trace_context: dict[str, Any] | None = None,
) -> QueryResult:
    """Handle transaction list queries."""
    started_at = time.perf_counter()
    query = contract.normalized_query
    cache_fingerprint = build_cache_fingerprint(query, account_id, account_ids, user_id=user_id)
    cache_scope_fingerprint = build_cache_scope_fingerprint(query, account_id, account_ids, user_id=user_id)
    (
        cached_transactions,
        cache_fetched_at,
        cached_fingerprint,
        cached_scope_fingerprint,
        cache_window_start,
        cache_window_end,
    ) = _coerce_session_cache(session_cache)
    cache_age_seconds = (time.time() - cache_fetched_at) if cache_fetched_at is not None else None
    current_window_start = query.time_range.start.isoformat() if query.time_range is not None else None
    current_window_end = query.time_range.end.isoformat() if query.time_range is not None else None
    cache_reuse = decide_transaction_cache_reuse(
        continuation_type=continuation_type,
        continuation_delta_type=continuation_delta_type,
        cached_transactions=cached_transactions,
        cache_age_seconds=cache_age_seconds,
        max_cache_age_seconds=TRANSACTION_CACHE_MAX_AGE_SECONDS,
        cached_fingerprint=cached_fingerprint,
        current_fingerprint=cache_fingerprint,
        cached_scope_fingerprint=cached_scope_fingerprint,
        current_scope_fingerprint=cache_scope_fingerprint,
        cache_window_start=cache_window_start,
        cache_window_end=cache_window_end,
        current_window_start=current_window_start,
        current_window_end=current_window_end,
    )
    can_reuse_cache = cache_reuse.can_reuse
    cache_strategy = cache_reuse.strategy
    fetch_account_count = len(account_ids) if query.accounts_scope == "all" and account_ids else 1

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
            trace_context=trace_context,
        )
    cache_fetched_at_value = cache_fetched_at if can_reuse_cache and cache_fetched_at is not None else time.time()
    scoped_transactions = apply_time_window(
        base_transactions,
        window_start=current_window_start,
        window_end=current_window_end,
    )
    transactions = apply_filters(scoped_transactions, query.filters) if query.filters else list(scoped_transactions)

    reverse_sort = query.result_reference != "oldest"
    transactions = sorted(transactions, key=_transaction_sort_key, reverse=reverse_sort)

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
                "counterparty": t.get("counterparty"),
                "counterparty_role": t.get("counterparty_role"),
                "recipient_name": t.get("recipient_name") or t.get("counterparty"),
                "recipient_account": t.get("recipient_account"),
                "recipient_account_number": t.get("recipient_account_number"),
                "recipient_bank_name": t.get("recipient_bank_name"),
                "recipient_bank_code": t.get("recipient_bank_code"),
                "source_account_id": t.get("source_account_id"),
                "source_account_label": t.get("source_account_label"),
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

    _log_query_trace(
        trace_context=trace_context,
        latency_ms=(time.perf_counter() - started_at) * 1000.0,
        cache_reused=can_reuse_cache,
        cache_strategy=cache_strategy,
        fetch_account_count=fetch_account_count,
    )
    return QueryResult(
        summary_text=result_summary,
        items=items,
        has_more=has_more,
        surface=surface,
        cached_transactions=base_transactions,
        cache_fetched_at=cache_fetched_at_value,
        cache_fingerprint=cache_fingerprint,
        cache_scope_fingerprint=cache_scope_fingerprint,
        cache_window_start=current_window_start,
        cache_window_end=current_window_end,
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
    trace_context: dict[str, Any] | None = None,
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
        trace_context=trace_context,
    )
