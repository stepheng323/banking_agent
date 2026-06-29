"""Analytics and breakdown handlers."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import (
    QueryExecutionContract,
    QueryResult,
    QueryResultItem,
    get_transaction_category,
)
from banking.transactions.query.services.fetching.fetch import (
    extract_counterparty,
    fetch_and_filter,
    parse_date,
)
from banking.transactions.query.utils.timezone import lagos_today
from banking.transactions.query.utils.totals import calculate_financial_totals
from shared.clients.abstractions.banking import BankDataProvider


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
    transactions = await fetch_and_filter(
        provider,
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
    )

    if not contract.aggregation:
        return QueryResult(summary_text=render_message("query.analytics.no_aggregation", language))

    agg_type = contract.aggregation.type

    totals = calculate_financial_totals(transactions, account_id)
    transactions = totals.settled_transactions

    if agg_type == "sum":
        total = (
            totals.total_inflow
            if contract.filters and contract.filters.transaction_type == "credit"
            else totals.total_outflow
        )
        if not contract.filters or contract.filters.transaction_type not in {"credit", "debit"}:
            total = totals.total_inflow + totals.total_outflow

        count = len(transactions)
        if count == 0:
            tx_type = contract.filters.transaction_type if contract.filters else None
            timeframe = _build_timeframe_suffix(contract, language)
            target_description = _build_sum_target_description(contract, language)
            if tx_type == "credit":
                return QueryResult(
                    summary_text=render_message(
                        "query.analytics.no_income",
                        language,
                        {"target_description": target_description, "timeframe": timeframe},
                    )
                )
            if tx_type == "debit":
                summary_key: MessageKey = (
                    "query.analytics.no_sent"
                    if contract.filters and _first_filter_value(contract.filters.counterparty)
                    else "query.analytics.no_spending"
                )
                return QueryResult(
                    summary_text=render_message(
                        summary_key,
                        language,
                        {"target_description": target_description, "timeframe": timeframe},
                    )
                )
            return QueryResult(summary_text=render_message("query.format.no_matching_transactions", language))
        target_description = _build_sum_target_description(contract, language)
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
                _sum_summary_key(contract),
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
        message_key: MessageKey = (
            "query.analytics.summary_count_zero" if count == 0 else "query.analytics.summary_count"
        )

        return QueryResult(
            summary_text=render_message(
                message_key,
                language,
                {
                    "count": count,
                    "timeframe": timeframe,
                    "transaction_label": _transaction_label(count, language),
                },
            ),
        )

    elif agg_type in ("largest", "smallest"):
        limit = contract.aggregation.limit or 5
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
                    "counterparty": t.get("counterparty")
                    or extract_counterparty(t.get("narration", ""), locale=language),
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
            if contract.filters and contract.filters.transaction_type == "debit"
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
        return await _aggregate_breakdown(transactions, contract, totals, language)

    return QueryResult(summary_text=render_message("query.analytics.aggregation_completed", language))


def _build_timeframe_suffix(query: QueryExecutionContract, locale: str) -> str:
    time_range = query.time_range
    if time_range:
        today = lagos_today()
        if time_range.start == time_range.end == today:
            return render_message("query.analytics.timeframe_today", locale)
        yesterday = today - timedelta(days=1)
        if time_range.start == time_range.end == yesterday:
            return render_message("query.analytics.timeframe_yesterday", locale)

        if time_range.granularity == "month":
            if time_range.start.month == today.month and time_range.start.year == today.year:
                return render_message("query.analytics.timeframe_this_month", locale)
            else:
                return render_message(
                    "query.analytics.timeframe_month", locale, {"month": time_range.start.strftime("%B %Y")}
                )

        if time_range.start == today - timedelta(days=30) and time_range.end == today:
            return render_message("query.analytics.timeframe_last_30_days", locale)

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


def _build_sum_target_description(query: QueryExecutionContract, locale: str) -> str:
    filters = query.filters
    if filters is None:
        return ""

    parts: list[str] = []
    counterparty = _display_filter_value(filters.counterparty)
    if counterparty:
        if filters.transaction_type == "debit":
            parts.append(
                render_message("query.analytics.target_counterparty_debit", locale, {"counterparty": counterparty})
            )
        elif filters.transaction_type == "credit":
            parts.append(
                render_message("query.analytics.target_counterparty_credit", locale, {"counterparty": counterparty})
            )
        else:
            parts.append(render_message("query.analytics.target_counterparty", locale, {"counterparty": counterparty}))
    elif filters.merchant:
        merchant = _first_filter_value(filters.merchant)
        if merchant:
            parts.append(render_message("query.analytics.target_merchant", locale, {"merchant": merchant}))

    account_filter = (filters.account_filter or "").strip()
    if account_filter:
        parts.append(render_message("query.analytics.target_account", locale, {"account": account_filter}))

    return "".join(parts)


def _sum_summary_key(query: QueryExecutionContract) -> MessageKey:
    filters = query.filters
    if filters is None:
        return "query.analytics.summary_spent"

    if filters.transaction_type == "credit":
        return "query.analytics.summary_received"

    if filters.transaction_type == "debit" and _first_filter_value(filters.counterparty):
        return "query.analytics.summary_sent"

    return "query.analytics.summary_spent"


def _first_filter_value(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _display_filter_value(values: list[str] | None) -> str | None:
    cleaned = _first_filter_value(values)
    if cleaned and cleaned == cleaned.lower():
        return cleaned.title()
    return cleaned


def _transaction_label(count: int, locale: str) -> str:
    if count == 1:
        return render_message("query.analytics.transaction_singular", locale)
    return render_message("query.analytics.transaction_plural", locale)


async def _aggregate_breakdown(
    transactions: list[dict],
    contract: QueryExecutionContract,
    totals: Any | None = None,
    language: str = "en",
) -> QueryResult:
    """Aggregate transactions by day/category/merchant."""
    if totals is None:
        totals = calculate_financial_totals(transactions)

    group_by = contract.aggregation.group_by if contract.aggregation else "day"
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "debit": 0,
            "credit": 0,
            "count": 0,
            "category_sources": set(),
            "account_suffix": None,
        }
    )

    for t in transactions:
        if group_by == "day":
            key = t.get("date", "")[:10]
        elif group_by == "category":
            resolved = get_transaction_category(t)
            key = "Other" if not resolved or resolved.lower() == "other" else resolved
        elif group_by == "merchant":
            key = t.get("counterparty") or extract_counterparty(t.get("narration", ""), locale=language)
        elif group_by == "account":
            source_account_number = str(t.get("source_account_number") or "").strip()
            suffix = f"···{source_account_number[-4:]}" if len(source_account_number) >= 4 else None
            key = t.get("source_account_label") or t.get("bank_name") or t.get("source_account_id") or "unknown account"
            if suffix:
                grouped[key]["account_suffix"] = suffix
        elif group_by == "transaction_type":
            key = t.get("type", "other")
        else:
            key = t.get("date", "")[:10]

        tx_type = str(t.get("type", "unknown")).strip().lower()
        if group_by == "category":
            source = t.get("category_source") or ("provider" if t.get("category") else "unknown")
            grouped[key]["category_sources"].add(str(source))
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

    # Apply limit if requested, default to 5 for breakdowns
    limit = contract.aggregation.limit if contract.aggregation and contract.aggregation.limit is not None else 5
    sorted_items = sorted_items[:limit]

    # Include total spent in summary if it's a category/merchant breakdown of expenses
    total_spent = totals.total_outflow

    items: list[QueryResultItem] = []
    for i, (key, data) in enumerate(sorted_items):
        display_key = key
        account_suffix = data.get("account_suffix")
        if group_by == "account" and isinstance(account_suffix, str) and account_suffix:
            display_key = f"{key} ({account_suffix})"
        metadata = {
            "debit": data["debit"],
            "credit": data["credit"],
            "count": data["count"],
            "key": key,  # Original key for drill-down
            "overall_total": total_spent,
        }
        if group_by == "category":
            category_sources = sorted(str(source) for source in data["category_sources"] if source)
            metadata["category_sources"] = category_sources
            metadata["category_confidence"] = "provider" if category_sources == ["provider"] else "inferred"
        if group_by == "account" and account_suffix:
            metadata["account_suffix"] = account_suffix
        items.append(
            QueryResultItem(
                id=str(i),
                description=display_key,
                amount=data["debit"] + data["credit"],
                date=parse_date(key) if group_by == "day" else date.today(),
                metadata=metadata,
            )
        )

    summary_msg = render_message(
        "query.analytics.breakdown_by",
        language,
        {"group_by": _breakdown_group_label(group_by, language)},
    )
    if contract.filters and contract.filters.transaction_type == "debit" and total_spent > 0:
        timeframe = _build_timeframe_suffix(contract, language)
        summary_msg = f"You spent ₦{total_spent:,.0f}{timeframe}. {summary_msg}"

    return QueryResult(
        summary_text=summary_msg,
        items=items,
    )


def _breakdown_group_label(group_by: str | None, language: str) -> str:
    """Render a human-friendly group-by label for summary text."""
    if group_by == "transaction_type":
        return render_message("query.analytics.group_by_transaction_type", language)
    return group_by or "day"
