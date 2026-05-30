"""Transaction-list presentation plan builders."""

from __future__ import annotations

from collections import OrderedDict

from apps.chat.src.agent.shared.query_contracts import PresentationMode, PresentationPlan, SurfaceView
from apps.chat.src.agent.workers.query.models.domain import QueryResult, QueryResultItem
from apps.chat.src.agent.workers.query.presentation.formatting import format_query_date, parse_summary_parts
from apps.chat.src.agent.workers.query.presentation.scope import build_transaction_heading
from apps.chat.src.agent.workers.query.presentation.surface_builder import result_query_contract
from banking.presentation.formatters.query_transaction_copy import format_transaction_list_item
from banking.presentation.i18n.renderer import render_message


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
    heading, contextual_heading_applied = _build_transaction_list_heading(
        result,
        summary_parts=summary_parts,
        locale=locale,
    )
    pagination = _build_transaction_list_pagination(
        result,
        summary_parts=summary_parts,
        locale=locale,
        current_page=current_page,
        show_expanded=show_expanded,
    )
    items = _build_transaction_list_lines(result.items or [], locale=locale, current_page=current_page)
    hint_lines: list[str] = []
    if pagination:
        hint_lines.append(f"_{pagination}_")
    if has_more:
        hint_lines.append(render_message("query.format.more_for_next_page", locale))

    return PresentationPlan(
        mode=PresentationMode.TRANSACTION_LIST,
        heading=heading if contextual_heading_applied or heading else result.summary_text,
        items=items,
        hint_text="\n".join(hint_lines) or None,
        selection_payloads=[item.payload for item in surface_view.items],
    )


def _build_transaction_list_heading(
    result: QueryResult,
    *,
    summary_parts: dict[str, str],
    locale: str,
) -> tuple[str, bool]:
    if result.summary_text and "—" in result.summary_text and result.summary_text.startswith("*"):
        return result.summary_text.split("\n", 1)[0], True

    heading = build_transaction_heading(result_query_contract(result), locale=locale)
    contextual_heading_applied = heading is not None
    if heading is None:
        heading = render_message("query.format.heading_transactions_default", locale)

    account_count = int(summary_parts.get("accounts", 1)) if summary_parts else 1
    if account_count > 1 and not contextual_heading_applied:
        heading = render_message(
            "query.format.transactions_across_accounts",
            locale,
            {"account_count": account_count},
        )
        contextual_heading_applied = True
    return heading, contextual_heading_applied


def _build_transaction_list_pagination(
    result: QueryResult,
    *,
    summary_parts: dict[str, str],
    locale: str,
    current_page: int,
    show_expanded: bool,
) -> str:
    if show_expanded and result.items:
        total_items = len(result.items)
        page_size = 5
        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, total_items)
        return render_message(
            "query.format.pagination_showing",
            locale,
            {"showing": f"{start_idx + 1}-{end_idx}", "total": total_items},
        )
    showing = summary_parts.get("showing", "")
    total = summary_parts.get("total", "")
    if showing and total:
        return render_message(
            "query.format.pagination_showing",
            locale,
            {"showing": showing, "total": total},
        )
    return ""


def _build_transaction_list_lines(
    items: list[QueryResultItem],
    *,
    locale: str,
    current_page: int,
) -> list[str]:
    lines: list[str] = []
    page_size = 5
    if len(items) > page_size:
        start_idx = current_page * page_size
        end_idx = start_idx + page_size
        display_items = items[start_idx:end_idx]
        remaining_count = len(items) - end_idx if end_idx < len(items) else 0
    else:
        display_items = items
        start_idx = current_page * page_size
        end_idx = start_idx + len(display_items)
        remaining_count = len(items) - end_idx if end_idx < len(items) else 0

    grouped = _group_items_by_date(display_items, locale=locale)
    for date_str, grouped_items in grouped.items():
        lines.append(f"*{date_str}*")
        for item in grouped_items:
            lines.append(format_transaction_list_item(item, locale=locale))
        lines.append("")

    if remaining_count > 0:
        lines.append(render_message("query.format.remaining_transactions", locale, {"count": remaining_count}))
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
