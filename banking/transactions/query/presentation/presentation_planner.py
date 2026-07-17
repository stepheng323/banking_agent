"""Presentation plan builders for query results."""

from __future__ import annotations

from typing import cast

from banking.presentation.formatters.currency import format_naira
from banking.presentation.formatters.query_transaction_copy import build_transaction_detail_lines
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import (
    PresentationMode,
    PresentationPlan,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import (
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
    TimeRange,
)
from banking.transactions.query.presentation.formatting import (
    format_query_amount,
    format_query_date,
    format_query_percentage,
    is_zero_structural_list_summary,
    parse_summary_parts,
)
from banking.transactions.query.presentation.scope import build_breakdown_heading, period_label
from banking.transactions.query.presentation.surface_builder import build_surface_view, result_query_contract
from banking.transactions.query.presentation.transaction_list_plan import (
    build_transaction_list_presentation_plan,
)
from banking.transactions.query.utils.timezone import lagos_today


def _build_raw_presentation_plan(
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

    if (
        surface_view.mode == SurfaceViewMode.DIRECT_ANSWER
        and isinstance(surface_view.context, dict)
        and surface_view.context.get("focus_type") == "beneficiary"
    ):
        return _build_beneficiary_summary_presentation_plan(
            result,
            display_items=surface_view.items,
            selection_payloads=[item.payload for item in surface_view.items],
            has_more=False,
            locale=locale,
        )

    if surface_view.mode == SurfaceViewMode.DIRECT_ANSWER:
        if _is_ranked_transaction_surface(result, surface_view=surface_view):
            return _build_ranked_transaction_presentation_plan(result, locale=locale, current_page=current_page)
        if isinstance(surface_view.context, dict) and surface_view.context.get("focus_type") in {
            "group_bucket",
            "account",
        }:
            return _build_focused_group_presentation_plan(result, surface_view=surface_view, locale=locale)
        return _build_single_item_detail_presentation_plan(result, locale=locale)

    if surface_view.mode == SurfaceViewMode.GROUPED_SUMMARY:
        query_contract = result_query_contract(result)
        context = surface_view.context if isinstance(surface_view.context, dict) else {}
        if str(context.get("view") or "").strip() == "beneficiary_summary":
            return _build_grouped_summary_presentation_plan(result, surface_view=surface_view, locale=locale)
        if len(surface_view.items) == 1 and query_contract and query_contract.result_limit == 1:
            item = surface_view.items[0]
            amount_str = f"₦{abs(float(item.amount or 0.0)):,.0f}"
            return PresentationPlan(
                mode=PresentationMode.DIRECT_ANSWER,
                lead_text=render_message("query.format.direct_answer_grouped_lead", locale, {"name": item.label}),
                evidence_lines=[amount_str],
                selection_payloads=[item.payload],
            )
        return _build_grouped_summary_presentation_plan(result, surface_view=surface_view, locale=locale)

    if _is_ranked_transaction_surface(result, surface_view=surface_view):
        return _build_ranked_transaction_presentation_plan(result, locale=locale, current_page=current_page)

    return build_transaction_list_presentation_plan(
        result,
        surface_view=surface_view,
        locale=locale,
        current_page=current_page,
        show_expanded=show_expanded,
        has_more=has_more,
    )


def build_presentation_plan(
    result: QueryResult,
    *,
    locale: str = "en",
    current_page: int = 0,
    show_expanded: bool = False,
    has_more: bool = False,
) -> PresentationPlan | None:
    """Build a typed presentation plan from the execution result, applying any conversational prefix."""
    plan = _build_raw_presentation_plan(
        result,
        locale=locale,
        current_page=current_page,
        show_expanded=show_expanded,
        has_more=has_more,
    )
    query_contract = result_query_contract(result)
    is_evidence_continuation = bool(
        query_contract and query_contract.continuation_type == "show_evidence"
    )
    if plan is not None and is_evidence_continuation and plan.mode == PresentationMode.TRANSACTION_LIST:
        visible_count = sum(item.lstrip().startswith("•") for item in plan.items)
        if visible_count == 1:
            plan.lead_text = render_message("query.format.evidence_lead_single", locale)
        else:
            plan.lead_text = render_message(
                "query.format.evidence_lead_plural",
                locale,
                {"count": visible_count},
            )
    elif plan is not None and getattr(result, "conversational_prefix", None):
        plan.lead_text = (
            f"{result.conversational_prefix} {plan.lead_text}"
            if plan.lead_text
            else result.conversational_prefix
        )
    return plan


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
        from banking.transactions.query.services.answers.fact_no_results import build_fact_no_results_text

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
    summary_parts = parse_summary_parts(result.summary_text)
    if is_zero_structural_list_summary(result.summary_text):
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
    current_page: int = 0,
) -> PresentationPlan:
    context = surface_view.context if isinstance(surface_view.context, dict) else {}
    page_size = 5
    start_idx = current_page * page_size
    display_items = surface_view.items[start_idx : start_idx + page_size]
    has_more = len(surface_view.items) > start_idx + page_size

    selection_payloads = [item.payload for item in display_items]
    summary_parts = parse_summary_parts(result.summary_text)

    if summary_parts and "accounts" in summary_parts and "showing" not in summary_parts:
        total = summary_parts.get("total", "₦0")
        hint = (
            render_message("query.format.show_more_hint", locale)
            if has_more
            else render_message("query.format.total_line", locale, {"total": total})
        )
        return PresentationPlan(
            mode=PresentationMode.SUMMARY_LIST,
            heading=render_message("query.format.accounts_header", locale),
            items=[
                render_message(
                    "query.format.accounts_item",
                    locale,
                    {
                        "amount": format_query_amount(item.amount or 0.0),
                        "bank_name": item.label,
                    },
                )
                for item in display_items
            ],
            hint_text=hint,
            selection_payloads=selection_payloads,
        )

    if str(context.get("view") or "").strip() == "beneficiary_summary":
        return _build_beneficiary_summary_presentation_plan(
            result,
            display_items=display_items,
            selection_payloads=selection_payloads,
            has_more=has_more,
            locale=locale,
        )

    group_by = str(context.get("group_by") or "").strip()
    if str(context.get("surface_type") or "").strip() == "breakdown":
        visible_total_abs = float(sum(abs(item.amount or 0.0) for item in surface_view.items))
        overall_total = float(sum(abs(item.amount or 0.0) for item in surface_view.items))
        total_abs = overall_total if overall_total > 0 else visible_total_abs

        has_more = len(surface_view.items) > start_idx + page_size
        hint_text = render_message("query.format.total_line", locale, {"total": format_naira(total_abs)})
        if has_more:
            hint_text += f"\n{render_message('query.format.show_more_hint', locale)}"

        summary = build_breakdown_heading(
            result_query_contract(result),
            group_by=group_by or None,
            locale=locale,
            fallback_summary=result.summary_text,
        )

        return PresentationPlan(
            mode=PresentationMode.SUMMARY_LIST,
            heading=render_message("query.format.breakdown_heading", locale, {"summary": summary}),
            items=[
                render_message(
                    "query.format.breakdown_item",
                    locale,
                    {
                        "amount": format_query_amount(item.amount or 0.0),
                        "name": item.label if group_by == "account" else item.label.replace("_", " ").title(),
                        "percentage": format_query_percentage(item.amount or 0.0, total_abs),
                        "count": item.count or int(item.metadata.get("count", 0)),
                    },
                )
                for item in display_items
            ],
            hint_text=hint_text,
            selection_payloads=selection_payloads,
        )

    return PresentationPlan(
        mode=PresentationMode.SUMMARY_LIST,
        heading=result.summary_text,
        selection_payloads=selection_payloads,
    )


def _build_beneficiary_summary_presentation_plan(
    result: QueryResult,
    *,
    display_items: list[SurfaceItemView],
    selection_payloads: list[SelectionPayload],
    has_more: bool,
    locale: str,
) -> PresentationPlan:
    query_contract = result_query_contract(result)
    tx_type = query_contract.filters.transaction_type if query_contract and query_contract.filters else "debit"
    sort_by = query_contract.aggregation.sort_by if query_contract and query_contract.aggregation else "amount"
    is_credit = tx_type == "credit"
    is_frequency = sort_by == "count"
    period = _beneficiary_summary_period_phrase(query_contract, result.summary_text, locale=locale)
    answer_only = bool(query_contract and query_contract.result_limit == 1)

    # If this is a time_delta or replace_scope continuation, we want to maintain the summary
    # context and avoid aggressively rendering a direct answer UI card just because there's 1 result.
    is_rescope = bool(
        query_contract and query_contract.continuation_delta_type in {"time_delta", "replace_scope"}
    )
    if is_rescope and answer_only:
        answer_only = False

    if answer_only:
        display_items = display_items[:1]
        selection_payloads = selection_payloads[:1]
        has_more = False

    lead_text = result.summary_text
    if display_items:
        first = display_items[0]
        first_name = _humanize_grouped_name(first.label)
        amount = format_naira(abs(float(first.amount or 0.0)))
        period_suffix = f" {period}" if period else ""
        if answer_only:
            if is_credit:
                lead_text = (
                    f"{first_name} sent you money most often{period_suffix}."
                    if is_frequency
                    else f"{first_name} sent you the most{period_suffix}: {amount}."
                )
            else:
                lead_text = (
                    f"You sent {first_name} money most often{period_suffix}."
                    if is_frequency
                    else f"You sent {first_name} the most{period_suffix}: {amount}."
                )
        else:
            count = len(display_items)
            noun = "sender" if is_credit else "recipient"
            noun = noun if count == 1 else f"{noun}s"
            if is_credit:
                lead_text = f"You received money from {count} {noun}{period_suffix}."
            else:
                lead_text = f"You sent money to {count} {noun}{period_suffix}."

    if answer_only:
        if display_items:
            item = display_items[0]
            count = item.count or int(item.metadata.get("count", 0))
            if count > 0:
                transfer_noun = "transfer" if count == 1 else "transfers"
                lead_text = f"{lead_text} ({count} {transfer_noun})"
        return PresentationPlan(
            mode=PresentationMode.DIRECT_ANSWER,
            lead_text=lead_text,
            selection_payloads=selection_payloads,
        )

    label = "top senders" if is_credit else "top recipients"
    if is_frequency:
        label = "most frequent senders" if is_credit else "most frequent recipients"

    hint = "Say a name to see the matching transactions."
    if has_more:
        hint = f"{render_message('query.format.show_more_hint', locale)}\n{hint}"

    return PresentationPlan(
        mode=PresentationMode.SUMMARY_LIST,
        lead_text=lead_text,
        items=[
            f"Your {label}:",
            *[_format_beneficiary_summary_item(item) for item in display_items],
        ],
        hint_text=hint,
        selection_payloads=selection_payloads,
    )


def _format_beneficiary_answer_detail(item: SurfaceItemView) -> str:
    count = item.count or int(item.metadata.get("count", 0))
    transfer_noun = "transfer" if count == 1 else "transfers"
    return f"{count} {transfer_noun}"


def _beneficiary_summary_period_phrase(
    query_contract: QueryExecutionContract | None,
    summary_text: str,
    *,
    locale: str,
) -> str:
    if query_contract is not None:
        label = period_label(query_contract.time_range, locale=locale)
        if label:
            return _sentence_period_label(label)

    if " this month" in summary_text.lower():
        return "this month"
    return ""


def _sentence_period_label(label: str) -> str:
    cleaned = " ".join(label.strip().split())
    if not cleaned:
        return ""
    if cleaned.lower() in {"today", "yesterday", "this week", "last week", "this month", "last month"}:
        return cleaned.lower()
    if "–" in cleaned or "-" in cleaned:
        return f"for {cleaned}"
    return f"in {cleaned}"


def _format_beneficiary_summary_item(item: SurfaceItemView) -> str:
    count = item.count or int(item.metadata.get("count", 0))
    transfer_noun = "transfer" if count == 1 else "transfers"
    return (
        f"{_humanize_grouped_name(item.label)} · "
        f"{format_naira(abs(float(item.amount or 0.0)))} · "
        f"{count} {transfer_noun}"
    )


def _humanize_grouped_name(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if cleaned and cleaned == cleaned.upper() and any(char.isalpha() for char in cleaned):
        return cleaned.title()
    return cleaned


def _build_focused_group_presentation_plan(
    result: QueryResult,
    *,
    surface_view: SurfaceView,
    locale: str,
) -> PresentationPlan:
    item = surface_view.items[0] if surface_view.items else None
    if item is None:
        return PresentationPlan(mode=PresentationMode.DIRECT_ANSWER, lead_text=result.summary_text)
    amount_str = format_naira(abs(float(item.amount or 0.0)))
    return PresentationPlan(
        mode=PresentationMode.DIRECT_ANSWER,
        lead_text=result.summary_text
        or render_message("query.format.direct_answer_grouped_lead", locale, {"name": item.label}),
        evidence_lines=[amount_str],
        selection_payloads=[item.payload],
    )


def _build_ranked_transaction_presentation_plan(
    result: QueryResult, *, locale: str, current_page: int = 0
) -> PresentationPlan:
    page_size = 5
    start_idx = current_page * page_size
    display_items = (result.items or [])[start_idx : start_idx + page_size]
    has_more = len(result.items or []) > start_idx + page_size

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
    for i, item in enumerate(display_items):
        rank = start_idx + i + 1
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
                    "amount": format_query_amount(item.amount or 0.0),
                    "name": item.description,
                    "date": format_query_date(item.date, locale=locale),
                    "bank_suffix": bank_suffix,
                },
            )
        )
    return PresentationPlan(
        mode=PresentationMode.SUMMARY_LIST,
        heading=heading,
        items=items,
        hint_text=render_message("query.format.show_more_hint", locale) if has_more else None,
        selection_payloads=[],
    )


def _build_single_item_detail_presentation_plan(result: QueryResult, *, locale: str) -> PresentationPlan | None:
    if not result.items or len(result.items) != 1:
        return None

    item = result.items[0]
    query_contract = result_query_contract(result)
    primary_text: str | None = None

    if query_contract is not None and query_contract.answer_fact_field is not None:
        from banking.transactions.query.services.answers.fact_answer import build_direct_fact_answer

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

    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    items = build_transaction_detail_lines(item, locale=locale, metadata=metadata)
    transaction_type = str(metadata.get("transaction_type") or "").strip()

    hint_text = None
    if transaction_type == "transfer":
        hint_text = render_message("query.format.transfer_reply_hint", locale)

    selection_payloads = (
        [surface_item.payload for surface_item in result.surface_view.items] if result.surface_view else []
    )
    return PresentationPlan(
        mode=PresentationMode.DIRECT_ANSWER,
        heading=primary_text or f"*{title}*",
        lead_text=f"*{title}*" if primary_text else None,
        items=items,
        hint_text=hint_text,
        selection_payloads=selection_payloads,
    )


def _is_ranked_transaction_surface(result: QueryResult, *, surface_view: SurfaceView) -> bool:
    if surface_view.mode == SurfaceViewMode.DIRECT_ANSWER and isinstance(surface_view.context, dict):
        if surface_view.context.get("focus_type") == "transaction" and surface_view.context.get("ranked_type") in {
            "largest",
            "smallest",
        }:
            return True
        return False
    if surface_view.mode != SurfaceViewMode.TRANSACTION_LIST:
        return False
    if isinstance(surface_view.context, dict) and surface_view.context.get("type") in ("largest", "smallest"):
        return True
    return any(item.metadata and item.metadata.get("rank") for item in result.items or [])
