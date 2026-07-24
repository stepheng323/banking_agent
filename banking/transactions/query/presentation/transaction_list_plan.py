"""Transaction-list presentation plan builders."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date

from banking.presentation.formatters.query_transaction_copy import format_transaction_list_item
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import PresentationMode, PresentationPlan, SurfaceView
from banking.transactions.query.models.domain import (
    QueryIntent,
    QueryRequest,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.presentation.formatting import format_query_date, parse_summary_parts
from banking.transactions.query.presentation.surface_builder import result_query_request
from banking.transactions.query.utils.timezone import lagos_today
from shared.utils.bank_aliases import display_bank_name

_PAGE_SIZE = 5


def build_transaction_list_presentation_plan(
    result: QueryResult,
    *,
    surface_view: SurfaceView,
    locale: str,
    current_page: int,
    show_expanded: bool,
    has_more: bool,
) -> PresentationPlan:
    summary_parts = parse_summary_parts(result.summary_text)
    display_items = _display_items(result.items or [], current_page=current_page)
    total_count = _result_total(summary_parts=summary_parts, items=result.items or [])
    has_more_results = (
        has_more
        or result.has_more
        or total_count
        > _display_end_index(
            display_count=len(display_items),
            current_page=current_page,
        )
    )
    lead_text = _build_transaction_list_lead(
        result,
        total_count=total_count,
        display_count=len(display_items),
        current_page=current_page,
        has_more=has_more_results,
    )
    items = _build_transaction_list_lines(display_items, locale=locale)
    hint_text = _more_for_next_page_text(locale) if has_more_results else None

    return PresentationPlan(
        mode=PresentationMode.TRANSACTION_LIST,
        lead_text=lead_text,
        items=items,
        hint_text=hint_text,
        selection_payloads=[item.payload for item in surface_view.items],
    )


def _display_items(
    items: list[QueryResultItem],
    *,
    current_page: int,
) -> list[QueryResultItem]:
    if len(items) <= _PAGE_SIZE:
        return items
    start_idx = max(current_page, 0) * _PAGE_SIZE
    return items[start_idx : start_idx + _PAGE_SIZE]


def _display_end_index(*, display_count: int, current_page: int) -> int:
    return max(current_page, 0) * _PAGE_SIZE + display_count


def _result_total(
    *,
    summary_parts: dict[str, str],
    items: list[QueryResultItem],
) -> int:
    try:
        return int(summary_parts.get("total", ""))
    except (TypeError, ValueError):
        return len(items)


def _build_transaction_list_lead(
    result: QueryResult,
    *,
    total_count: int,
    display_count: int,
    current_page: int,
    has_more: bool,
) -> str:
    contract = result_query_request(result)
    if current_page > 0:
        return _page_window_copy(display_count=display_count, current_page=current_page)
    if contract and contract.intent == QueryIntent.ANALYTICS_SUMMARY and result.summary_text:
        sentence = result.summary_text
    else:
        noun = _transaction_noun(contract, count=total_count)
        count_text = "one" if total_count == 1 else str(total_count)
        sentence = f"I found {count_text} {noun}"
        time_phrase = _transaction_list_time_phrase(contract)
        if time_phrase:
            sentence = f"{sentence} {time_phrase}"
        sentence = f"{sentence}."
    if total_count > display_count or has_more:
        page_copy = _page_window_copy(display_count=display_count, current_page=current_page)
        if page_copy:
            sentence = f"{sentence} {page_copy}"
    return sentence


def _transaction_noun(contract: QueryRequest | None, *, count: int) -> str:
    filters = contract.filters if contract else None
    account_filter = (filters.account_filter or "").strip() if filters else ""
    category = _first_filter_label(filters.category if filters else None)
    status = filters.status if filters else None
    tx_type = filters.transaction_type if filters else None

    parts: list[str] = []
    if status:
        parts.append(status)
    if account_filter:
        parts.append(display_bank_name(account_filter) or account_filter)
    if category:
        parts.append(category)
    elif not account_filter and tx_type in {"credit", "debit"}:
        parts.append(tx_type)

    base = f"{' '.join(parts)} transaction" if parts else "transaction"
    return base if count == 1 else f"{base}s"


def _first_filter_label(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = " ".join(str(value or "").strip().split())
        if cleaned:
            return cleaned if cleaned != cleaned.lower() else cleaned.title()
    return None


def _transaction_list_time_phrase(contract: QueryRequest | None) -> str:
    time_range = _contract_time_range(contract)
    if time_range is None:
        return ""

    today = lagos_today()
    start = time_range.start
    end = time_range.end
    if start == end == today:
        return "today"
    if start == end:
        return f"for {_format_lead_date(start)}"
    if end == today and (end - start).days in {29, 30}:
        return "in the last 30 days"
    if end == today and start == today.replace(day=1):
        return "this month"
    return f"for {_format_lead_date(start)}–{_format_lead_date(end)}"


def _contract_time_range(contract: QueryRequest | None) -> TimeRange | None:
    if contract is None:
        return None
    if contract.time_range is not None:
        return contract.time_range
    if contract.time_start and contract.time_end:
        return TimeRange(start=contract.time_start, end=contract.time_end)
    return None


def _format_lead_date(value: date) -> str:
    return value.strftime("%b %d").replace(" 0", " ")


def _page_window_copy(*, display_count: int, current_page: int) -> str:
    if display_count <= 0:
        return ""
    if current_page <= 0:
        return "Here is the first one." if display_count == 1 else f"Here are the first {display_count}."
    return f"Here is {display_count} more." if display_count == 1 else f"Here are {display_count} more."


def _more_for_next_page_text(locale: str) -> str:
    if locale == "en":
        return "More for next page"
    text = render_message("query.format.more_for_next_page", locale)
    for token in ("**", "*"):
        text = text.replace(token, "")
    return "\n".join(line.strip().strip("_").strip() for line in text.splitlines()).strip()


def _build_transaction_list_lines(items: list[QueryResultItem], *, locale: str) -> list[str]:
    lines: list[str] = []
    grouped = _group_items_by_date(items, locale=locale)
    for date_str, grouped_items in grouped.items():
        lines.append(f"*{date_str}*")
        for item in grouped_items:
            lines.append(format_transaction_list_item(item, locale=locale, include_date=False))
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _group_items_by_date(
    items: list[QueryResultItem],
    *,
    locale: str,
) -> dict[str, list[QueryResultItem]]:
    grouped: dict[str, list[QueryResultItem]] = OrderedDict()
    for item in items:
        date_key = format_query_date(item.date, locale=locale)
        grouped.setdefault(date_key, []).append(item)
    return grouped
