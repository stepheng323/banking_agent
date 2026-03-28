"""Analytics and breakdown handlers."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from apps.core.src.agent.graphs.query.models import (
    NormalizedQuery,
    QueryExecutionContract,
    QueryResult,
    QueryResultItem,
    get_transaction_category,
)
from apps.core.src.agent.graphs.query.services.fetch import (
    extract_counterparty,
    fetch_and_filter,
    parse_date,
)
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from shared.clients.abstractions.banking import BankDataProvider
from shared.i18n import render_message


async def handle_analytics(
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
    """Handle analytics summary queries."""
    query = contract.normalized_query
    transactions = await fetch_and_filter(
        provider,
        query,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
        language=language,
    )

    if not query.aggregation:
        return QueryResult(summary_text=render_message("query.analytics.no_aggregation", language))

    agg_type = query.aggregation.type

    if agg_type == "sum":
        total = sum(abs(t.get("amount", 0)) for t in transactions)
        count = len(transactions)
        if count == 0:
            tx_type = query.filters.transaction_type if query.filters else None
            timeframe = _build_timeframe_suffix(contract, language)
            if tx_type == "credit":
                return QueryResult(
                    summary_text=render_message("query.analytics.no_income", language, {"timeframe": timeframe})
                )
            if tx_type == "debit":
                return QueryResult(
                    summary_text=render_message("query.analytics.no_spending", language, {"timeframe": timeframe})
                )
            return QueryResult(summary_text=render_message("query.format.no_matching_transactions", language))
        merchant = query.filters.merchant[0] if query.filters and query.filters.merchant else None

        target_description = (
            render_message("query.analytics.target_merchant", language, {"merchant": merchant}) if merchant else ""
        )
        timeframe = _build_timeframe_suffix(contract, language)

        items = [
            QueryResultItem(
                id=t.get("id", "")[:8] if t.get("id") else str(i),
                description=t.get("narration", render_message("query.common.transaction", language)),
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
            summary_text=render_message(
                "query.analytics.summary_spent",
                language,
                {
                    "total": f"{total:,.0f}",
                    "target_description": target_description,
                    "timeframe": timeframe,
                    "count": count,
                    "transaction_label": _transaction_label(count, language),
                },
            ),
            items=items,
        )

    elif agg_type == "average":
        if transactions:
            avg = sum(abs(t.get("amount", 0)) for t in transactions) / len(transactions)
            count = len(transactions)

            timeframe = _build_timeframe_suffix(contract, language)

            items = [
                QueryResultItem(
                    id=t.get("id", "")[:8] if t.get("id") else str(i),
                    description=t.get("narration", render_message("query.common.transaction", language)),
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
                summary_text=render_message(
                    "query.analytics.summary_average",
                    language,
                    {
                        "average": f"{int(avg):,}",
                        "timeframe": timeframe,
                        "count": count,
                    },
                ),
                items=items,
            )
        return QueryResult(summary_text=render_message("query.analytics.no_transactions", language))

    elif agg_type == "count":
        count = len(transactions)
        timeframe = _build_timeframe_suffix(contract, language)

        return QueryResult(
            summary_text=render_message(
                "query.analytics.summary_count",
                language,
                {"count": count, "timeframe": timeframe},
            ),
        )

    elif agg_type in ("largest", "smallest"):
        limit = query.aggregation.limit or 5
        reverse_sort = agg_type == "largest"

        sorted_txns = sorted(transactions, key=lambda t: abs(t.get("amount", 0)), reverse=reverse_sort)

        timeframe = _build_timeframe_suffix(contract, language)

        # Pagination for ranked surface:
        # Page 0: Uses 'limit' (e.g. 1)
        # Page N: Uses 'page_size' (e.g. 5) starting from where limit ended
        start_idx = 0
        end_idx = limit

        if current_page > 0:
            start_idx = limit + ((current_page - 1) * page_size)
            end_idx = start_idx + page_size

        paginated_txns = sorted_txns[start_idx:end_idx]

        items = [
            QueryResultItem(
                id=t.get("id", "")[:8] if t.get("id") else str(i),
                description=t.get("narration", render_message("query.common.transaction", language)),
                amount=abs(t.get("amount", 0)),
                date=parse_date(t.get("date", "")),
                metadata={
                    "bank_name": t.get("bank_name", ""),
                    "type": t.get("type", ""),
                    "counterparty": t.get("counterparty") or extract_counterparty(t.get("narration", ""), locale=language),
                    "rank": start_idx + i + 1,  # Strict ranking
                },
            )
            for i, t in enumerate(paginated_txns)
        ]

        # Only return single item surface on FIRST page if limit=1
        if limit == 1 and current_page == 0 and items:
            adjective = render_message(
                "query.analytics.adjective_biggest" if agg_type == "largest" else "query.analytics.adjective_smallest",
                language,
            )

            return QueryResult(
                summary_text=render_message(
                    "query.analytics.single_expense",
                    language,
                    {"adjective": adjective, "timeframe": timeframe},
                ),
                items=items,
            )

        label = render_message(
            "query.analytics.label_expenses"
            if query.filters and query.filters.transaction_type == "debit"
            else "query.analytics.label_transactions",
            language,
        )
        adj_title = render_message(
            "query.analytics.title_largest" if agg_type == "largest" else "query.analytics.title_smallest",
            language,
        )
        adjective = render_message(
            "query.analytics.adjective_biggest" if agg_type == "largest" else "query.analytics.adjective_smallest",
            language,
        )

        if current_page > 0:
            summary_text = render_message(
                "query.analytics.ranked_other",
                language,
                {"adjective": adjective, "start": start_idx + 1, "end": end_idx},
            )
        else:
            summary_text = render_message(
                "query.analytics.ranked_top",
                language,
                {"limit": limit, "adj_title": adj_title, "label": label},
            )

        total = len(sorted_txns)
        has_more = end_idx < total

        return QueryResult(summary_text=summary_text, items=items, has_more=has_more)

    elif agg_type == "breakdown":
        return await _aggregate_breakdown(transactions, query, language)

    return QueryResult(summary_text=render_message("query.analytics.aggregation_completed", language))


def _build_timeframe_suffix(query: QueryExecutionContract, locale: str) -> str:
    time_range = query.normalized_query.time_range
    if time_range:
        today = lagos_today()
        if time_range.start == time_range.end == today:
            return render_message("query.analytics.timeframe_today", locale)
        yesterday = today - timedelta(days=1)
        if time_range.start == time_range.end == yesterday:
            return render_message("query.analytics.timeframe_yesterday", locale)
        if time_range.start == time_range.end:
            return render_message(
                "query.analytics.timeframe_on_date",
                locale,
                {"date": time_range.start.strftime("%b %d")},
            )
        return render_message(
            "query.analytics.timeframe_range",
            locale,
            {
                "start": time_range.start.strftime("%b %d"),
                "end": time_range.end.strftime("%b %d"),
            },
        )
    return render_message("query.analytics.timeframe_default", locale)


def _transaction_label(count: int, locale: str) -> str:
    if count == 1:
        return render_message("query.analytics.transaction_singular", locale)
    return render_message("query.analytics.transaction_plural", locale)


async def _aggregate_breakdown(transactions: list[dict], query: NormalizedQuery, language: str = "en") -> QueryResult:
    """Aggregate transactions by day/category/merchant."""
    group_by = query.aggregation.group_by if query.aggregation else "day"
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"debit": 0, "credit": 0, "count": 0})

    for t in transactions:
        if group_by == "day":
            key = t.get("date", "")[:10]
        elif group_by == "category":
            key = get_transaction_category(t) or "other"
        elif group_by == "merchant":
            key = t.get("counterparty") or extract_counterparty(t.get("narration", ""), locale=language)
        elif group_by == "account":
            key = (
                t.get("source_account_label")
                or t.get("bank_name")
                or t.get("source_account_id")
                or "unknown account"
            )
        elif group_by == "transaction_type":
            key = t.get("type", "other")
        else:
            key = t.get("date", "")[:10]

        tx_type = t.get("type", "unknown")
        if tx_type in ("debit", "credit"):
            grouped[key][tx_type] += t.get("amount", 0)
            grouped[key]["count"] += 1

    # Sort: Amount (desc) -> Count (desc)
    # Special case: "Other" always goes to the bottom
    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, int]:
        key, data = item
        if key.lower() == "other":
            return (-1.0, 0)
        return (abs(float(data["debit"]) + float(data["credit"])), int(data["count"]))

    sorted_items = sorted(grouped.items(), key=sort_key, reverse=True)

    # Apply limit if requested
    limit = query.aggregation.limit if query.aggregation and query.aggregation.limit is not None else 10
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

    return QueryResult(
        summary_text=render_message(
            "query.analytics.breakdown_by",
            language,
            {"group_by": _breakdown_group_label(group_by, language)},
        ),
        items=items,
    )


def _breakdown_group_label(group_by: str | None, language: str) -> str:
    """Render a human-friendly group-by label for summary text."""
    if group_by == "transaction_type":
        return render_message("query.analytics.group_by_transaction_type", language)
    return group_by or "day"
