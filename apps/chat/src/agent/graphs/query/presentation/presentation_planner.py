"""Presentation plan builders for query results."""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date, datetime
from typing import cast

from apps.chat.src.agent.graphs.query.models import (
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.chat.src.agent.graphs.query.presentation.surface_builder import build_surface_view, result_query_contract
from apps.chat.src.agent.graphs.query.services.presentation_scope import (
    build_breakdown_heading,
    build_transaction_heading,
)
from apps.chat.src.agent.graphs.query.utils.timezone import lagos_today
from apps.chat.src.agent.shared.query_contracts import (
    PresentationMode,
    PresentationPlan,
    SurfaceView,
    SurfaceViewMode,
)
from shared.i18n import render_message
from shared.i18n.message_keys import MessageKey


def build_presentation_plan(
    result: QueryResult,
    *,
    locale: str = "en",
    current_page: int = 0,
    show_expanded: bool = False,
    has_more: bool = False,
) -> PresentationPlan | None:
    """Build a typed presentation plan from the execution result."""
    if result.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER:
        direct_plan = _build_direct_answer_presentation_plan(result, locale=locale)
        if direct_plan is not None:
            return direct_plan

    if not result.items:
        no_results_plan = _build_no_results_presentation_plan(result, locale=locale)
        if no_results_plan is not None:
            return no_results_plan

    surface_view = result.surface_view or build_surface_view(result)
    if surface_view is None:
        return None

    if result.answer_strategy == QueryAnswerStrategy.CLARIFY and result.answer_context is not None:
        return PresentationPlan(
            mode=PresentationMode.CLARIFY,
            lead_text=result.answer_context.primary_text,
            selection_payloads=[item.payload for item in surface_view.items],
        )

    if surface_view.mode == SurfaceViewMode.DIRECT_ANSWER:
        return _build_single_item_detail_presentation_plan(result, locale=locale)

    if surface_view.mode == SurfaceViewMode.GROUPED_SUMMARY:
        return _build_grouped_summary_presentation_plan(result, surface_view=surface_view, locale=locale)

    if _is_ranked_transaction_surface(result, surface_view=surface_view):
        return _build_ranked_transaction_presentation_plan(result, locale=locale)

    return _build_transaction_list_presentation_plan(
        result,
        surface_view=surface_view,
        locale=locale,
        current_page=current_page,
        show_expanded=show_expanded,
        has_more=has_more,
    )


def _build_transaction_list_presentation_plan(
    result: QueryResult,
    *,
    surface_view: SurfaceView,
    locale: str,
    current_page: int,
    show_expanded: bool,
    has_more: bool,
) -> PresentationPlan:
    summary_parts = _parse_summary_parts(result.summary_text)
    heading, contextual_heading_applied = _build_transaction_list_heading(result, summary_parts=summary_parts, locale=locale)
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


def _build_direct_answer_presentation_plan(result: QueryResult, *, locale: str) -> PresentationPlan | None:
    if result.answer_context is not None:
        evidence_lines = []
        if result.answer_context.secondary_text:
            evidence_lines.append(result.answer_context.secondary_text)
        return PresentationPlan(
            mode=PresentationMode.DIRECT_ANSWER,
            lead_text=result.answer_context.primary_text,
            evidence_lines=evidence_lines,
            hint_text=result.answer_context.hint_text,
            selection_payloads=[],
        )

    if not result.items:
        from apps.chat.src.agent.graphs.query.services.answer_strategy import build_fact_no_results_text

        fact_no_results = build_fact_no_results_text(result_query_contract(result), locale=locale)
        if fact_no_results:
            return PresentationPlan(mode=PresentationMode.DIRECT_ANSWER, lead_text=fact_no_results)
        if result.summary_text:
            return PresentationPlan(mode=PresentationMode.DIRECT_ANSWER, lead_text=result.summary_text)
        no_results_text = _build_no_results_text(result, locale=locale)
        if no_results_text:
            return PresentationPlan(mode=PresentationMode.DIRECT_ANSWER, lead_text=no_results_text)
    return None


def _build_no_results_presentation_plan(result: QueryResult, *, locale: str) -> PresentationPlan | None:
    summary_parts = _parse_summary_parts(result.summary_text)
    query_contract = result_query_contract(result)
    if (
        summary_parts
        and summary_parts.get("showing") is not None
        and str(summary_parts.get("total", "")).strip() == "0"
        and query_contract is not None
        and query_contract.intent in {QueryIntent.TRANSACTION_LIST, QueryIntent.TRANSACTION_SEARCH}
    ):
        no_results_text = _build_no_results_text(result, locale=locale)
        if no_results_text is not None:
            return PresentationPlan(mode=PresentationMode.DIRECT_ANSWER, lead_text=no_results_text)

    if result.summary_text and not summary_parts:
        return PresentationPlan(mode=PresentationMode.DIRECT_ANSWER, lead_text=result.summary_text)
    no_results_text = _build_no_results_text(result, locale=locale)
    if no_results_text is None:
        return None
    return PresentationPlan(mode=PresentationMode.DIRECT_ANSWER, lead_text=no_results_text)


def _build_no_results_text(result: QueryResult, *, locale: str) -> str | None:
    query_contract = result_query_contract(result)
    time_range = query_contract.time_range if query_contract else None
    tx_type = query_contract.filters.transaction_type if query_contract and query_contract.filters else None
    if (
        query_contract
        and query_contract.intent in {QueryIntent.TRANSACTION_LIST, QueryIntent.TRANSACTION_SEARCH}
        and time_range is not None
        and not _has_search_shaped_no_results_context(query_contract)
    ):
        return _format_factual_no_results(time_range, locale=locale, transaction_type=tx_type)

    if tx_type not in ("credit", "debit"):
        return render_message("query.format.no_matching_transactions", locale)

    time_suffix = ""
    if time_range:
        if time_range.start == time_range.end == lagos_today():
            time_suffix = render_message("query.format.no_results_time_suffix_today", locale)
        else:
            time_suffix = render_message("query.format.no_results_time_suffix_period", locale)

    return render_message(
        "query.format.no_results_with_type",
        locale,
        {"transaction_type": tx_type, "time_suffix": time_suffix},
    )


def _has_search_shaped_no_results_context(query_contract: QueryExecutionContract | None) -> bool:
    if not query_contract:
        return False
    if query_contract.aggregation is not None:
        return True

    filters = query_contract.filters
    if not filters:
        return False

    return any(
        (
            bool(filters.category),
            bool(filters.merchant),
            bool(filters.counterparty),
            filters.min_amount is not None,
            filters.max_amount is not None,
            bool(filters.exclude),
            filters.account_filter is not None,
            query_contract.account_name is not None,
        )
    )


def _format_factual_no_results(
    time_range: TimeRange,
    *,
    locale: str,
    transaction_type: str | None,
) -> str:
    today = lagos_today()
    yesterday = today.fromordinal(today.toordinal() - 1)

    if time_range.start == time_range.end == today:
        suffix = "today"
    elif time_range.start == time_range.end == yesterday:
        suffix = "yesterday"
    else:
        suffix = "period"

    if transaction_type in ("credit", "debit"):
        return render_message(
            cast(MessageKey, f"query.format.no_transactions_with_type_{suffix}"),
            locale,
            {"transaction_type": transaction_type},
        )

    return render_message(cast(MessageKey, f"query.format.no_transactions_{suffix}"), locale)


def _build_grouped_summary_presentation_plan(
    result: QueryResult,
    *,
    surface_view: SurfaceView,
    locale: str,
) -> PresentationPlan:
    context = surface_view.context if isinstance(surface_view.context, dict) else {}
    selection_payloads = [item.payload for item in surface_view.items]
    summary_parts = _parse_summary_parts(result.summary_text)

    if summary_parts and "accounts" in summary_parts and "showing" not in summary_parts:
        total = summary_parts.get("total", "₦0")
        return PresentationPlan(
            mode=PresentationMode.SUMMARY_LIST,
            heading=render_message("query.format.accounts_header", locale),
            items=[
                render_message(
                    "query.format.accounts_item",
                    locale,
                    {
                        "amount": _format_amount(item.amount or 0.0),
                        "bank_name": item.label,
                    },
                )
                for item in surface_view.items
            ],
            hint_text=render_message("query.format.total_line", locale, {"total": total}),
            selection_payloads=selection_payloads,
        )

    if str(context.get("view") or "").strip() == "beneficiary_summary":
        return PresentationPlan(
            mode=PresentationMode.SUMMARY_LIST,
            heading=result.summary_text,
            items=[
                render_message(
                    "query.beneficiary.summary_line",
                    locale,
                    {
                        "name": item.label,
                        "total": f"{abs(float(item.amount or 0.0)):,.0f}",
                        "count": item.count or int(item.metadata.get("count", 0)),
                    },
                )
                for item in surface_view.items
            ],
            hint_text=render_message("query.beneficiary.reply_name_hint", locale),
            selection_payloads=selection_payloads,
        )

    group_by = str(context.get("group_by") or "").strip()
    if str(context.get("surface_type") or "").strip() == "breakdown":
        total_abs = float(sum(abs(item.amount or 0.0) for item in surface_view.items))
        return PresentationPlan(
            mode=PresentationMode.SUMMARY_LIST,
            heading=build_breakdown_heading(
                result_query_contract(result),
                group_by=group_by or None,
                locale=locale,
                fallback_summary=result.summary_text,
            ),
            items=[
                render_message(
                    "query.format.breakdown_item",
                    locale,
                    {
                        "amount": _format_amount(item.amount or 0.0),
                        "name": item.label if group_by == "account" else item.label.replace("_", " ").title(),
                        "percentage": _format_percentage(item.amount or 0.0, total_abs),
                        "count": item.count or int(item.metadata.get("count", 0)),
                    },
                )
                for item in surface_view.items
            ],
            hint_text=render_message("query.format.total_line", locale, {"total": f"₦{total_abs:,.0f}"}),
            selection_payloads=selection_payloads,
        )

    return PresentationPlan(
        mode=PresentationMode.SUMMARY_LIST,
        heading=result.summary_text,
        selection_payloads=selection_payloads,
    )


def _build_ranked_transaction_presentation_plan(result: QueryResult, *, locale: str) -> PresentationPlan:
    heading = (
        result.summary_text
        if result.summary_text and result.summary_text.startswith("🏆")
        else render_message(
            "query.format.ranked_heading",
            locale,
            {"summary": result.summary_text or render_message("query.common.transaction", locale)},
        )
    )
    items: list[str] = []
    for i, item in enumerate(result.items or []):
        rank = i + 1
        if item.metadata and item.metadata.get("rank"):
            rank = int(item.metadata["rank"])
        bank_suffix = ""
        if item.metadata and item.metadata.get("bank_name"):
            bank_suffix = f" _({item.metadata['bank_name']})_"
        items.append(
            render_message(
                "query.format.ranked_item",
                locale,
                {
                    "rank": rank,
                    "amount": _format_amount(item.amount or 0.0),
                    "name": item.description,
                    "date": _format_date(item.date, locale=locale),
                    "bank_suffix": bank_suffix,
                },
            )
        )
    return PresentationPlan(
        mode=PresentationMode.SUMMARY_LIST,
        heading=heading,
        items=items,
        selection_payloads=[],
    )


def _build_single_item_detail_presentation_plan(result: QueryResult, *, locale: str) -> PresentationPlan | None:
    if not result.items or len(result.items) != 1:
        return None

    item = result.items[0]
    query_contract = result_query_contract(result)
    primary_text: str | None = None

    if query_contract is not None and query_contract.answer_fact_field is not None:
        from apps.chat.src.agent.graphs.query.services.answer_strategy import build_direct_fact_answer

        answer_context = build_direct_fact_answer(
            item,
            query_contract=query_contract,
            fact_field=query_contract.answer_fact_field,
            locale=locale,
        )
        primary_text = answer_context.primary_text

    title = render_message("query.format.transaction_details_title", locale)
    if query_contract and query_contract.result_reference == "latest":
        title = render_message("query.format.last_transaction_title", locale)
        tx_filters = query_contract.filters
        if tx_filters and tx_filters.transaction_type in ("debit", "credit"):
            title = render_message(
                "query.format.last_transaction_type_title",
                locale,
                {"transaction_type": tx_filters.transaction_type},
            )

    items: list[str] = [
        render_message("query.format.field_amount", locale, {"amount": f"₦{float(item.amount):,.2f}"}),
        render_message("query.format.field_description", locale, {"description": item.description}),
        render_message(
            "query.format.field_date",
            locale,
            {
                "date": item.date.strftime("%B %d, %Y")
                if item.date
                else render_message("query.format.unknown", locale),
            },
        ),
    ]

    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    tx_type = str(metadata.get("type") or "").strip()
    if tx_type:
        direction = (
            render_message("query.format.type_outgoing_debit", locale)
            if tx_type == "debit"
            else render_message("query.format.type_incoming_credit", locale)
        )
        items.append(render_message("query.format.field_type", locale, {"type": direction}))

    bank_name = str(metadata.get("bank_name") or "").strip()
    if bank_name:
        items.append(render_message("query.format.field_bank", locale, {"bank_name": bank_name}))

    transaction_type = str(metadata.get("transaction_type") or "").strip()
    if transaction_type:
        items.append(
            render_message(
                "query.format.field_category",
                locale,
                {"category": transaction_type.title()},
            )
        )

    status = str(metadata.get("status") or "").strip()
    if status:
        status_display = (
            render_message("query.format.status_success", locale)
            if status.lower() in ("success", "completed", "successful")
            else render_message("query.format.status_pending_generic", locale, {"status": status.title()})
        )
        items.append(render_message("query.format.field_status", locale, {"status": status_display}))

    reference = _display_reference(item)
    if reference:
        items.append(render_message("query.format.field_ref", locale, {"reference": reference}))

    hint_text = None
    if transaction_type == "transfer":
        hint_text = render_message("query.format.transfer_reply_hint", locale)

    selection_payloads = [surface_item.payload for surface_item in result.surface_view.items] if result.surface_view else []
    return PresentationPlan(
        mode=PresentationMode.DIRECT_ANSWER,
        heading=primary_text or f"*{title}*",
        lead_text=f"*{title}*" if primary_text else None,
        items=items,
        hint_text=hint_text,
        selection_payloads=selection_payloads,
    )


def _format_amount(amount: float) -> str:
    amount = abs(amount)
    if amount >= 1000:
        return f"₦{amount:,.0f}"
    return f"₦{amount:.0f}"


def _display_reference(item: QueryResultItem) -> str | None:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    for key in ("transaction_id", "reference", "ref"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value

    item_id = str(item.id or "").strip()
    if not item_id or re.fullmatch(r"\d+", item_id):
        return None
    return item_id


def _format_percentage(amount: float, total_abs: float) -> str:
    if total_abs <= 0:
        return "0%"
    pct = (abs(amount) / total_abs) * 100
    if 0 < pct < 1:
        return "<1%"
    return f"{int(pct)}%"


def _parse_summary_parts(summary_text: str | None) -> dict[str, str]:
    if not summary_text or "|" not in summary_text:
        return {}
    parts: dict[str, str] = {}
    for part in summary_text.split("|"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            parts[key] = value
    return parts


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
            lines.append(_format_transaction_list_item(item, locale=locale))
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
        date_key = _format_date(item.date, locale=locale)
        grouped.setdefault(date_key, []).append(item)
    return grouped


def _format_transaction_list_item(item: QueryResultItem, *, locale: str) -> str:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    counterparty = metadata.get("counterparty")
    tx_type = str(metadata.get("type") or "").strip().lower()
    real_type = metadata.get("transaction_type")
    amount = _format_amount(item.amount)

    if real_type in ("airtime", "data"):
        recipient = counterparty or _extract_phone_recipient(item.description)
        narration = render_message(
            "query.format.narration.type_for_recipient",
            locale,
            {"type": str(real_type).title(), "recipient": recipient or render_message("query.format.recipient_fallback", locale)},
        )
    elif isinstance(counterparty, str) and counterparty.strip():
        if "transfer" in item.description.lower():
            prefix = (
                render_message("query.format.narration.transfer_from", locale)
                if tx_type == "credit"
                else render_message("query.format.narration.transfer_to", locale)
            )
            narration = f"{prefix} {counterparty}"
        else:
            narration = counterparty
    else:
        narration = item.description or render_message("query.format.narration.transaction", locale)

    label = (
        render_message("query.format.label_received", locale)
        if tx_type == "credit"
        else render_message("query.format.label_sent", locale)
    )
    bank_name = str(metadata.get("bank_name") or "").strip()
    if bank_name:
        return render_message(
            "query.format.transaction_item_with_bank",
            locale,
            {"amount": amount, "label": label, "narration": narration, "bank_name": bank_name},
        )
    return render_message(
        "query.format.transaction_item",
        locale,
        {"amount": amount, "label": label, "narration": narration},
    )


def _format_date(value: date | str, *, locale: str) -> str:
    if isinstance(value, str):
        raw_date = value
        try:
            value = datetime.strptime(value[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return raw_date[:10] if raw_date else render_message("query.format.unknown", locale)
    return value.strftime("%b %d").replace(" 0", " ")


def _extract_phone_recipient(description: str) -> str | None:
    phone_match = re.search(r"(\d{10,11})", description or "")
    return phone_match.group(1) if phone_match else None


def _is_ranked_transaction_surface(result: QueryResult, *, surface_view: SurfaceView) -> bool:
    if surface_view.mode != SurfaceViewMode.TRANSACTION_LIST:
        return False
    if isinstance(surface_view.context, dict) and surface_view.context.get("type") in ("largest", "smallest"):
        return True
    return any(item.metadata and item.metadata.get("rank") for item in result.items or [])
