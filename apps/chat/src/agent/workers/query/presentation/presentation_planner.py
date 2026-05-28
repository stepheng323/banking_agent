"""Presentation plan builders for query results."""

from __future__ import annotations

from typing import cast

from apps.chat.src.agent.shared.query_contracts import (
    PresentationMode,
    PresentationPlan,
    SurfaceView,
    SurfaceViewMode,
)
from apps.chat.src.agent.workers.query.models.domain import (
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
    TimeRange,
)
from apps.chat.src.agent.workers.query.presentation.formatting import (
    format_query_amount,
    format_query_date,
    format_query_percentage,
    parse_summary_parts,
)
from apps.chat.src.agent.workers.query.presentation.scope import build_breakdown_heading
from apps.chat.src.agent.workers.query.presentation.surface_builder import build_surface_view, result_query_contract
from apps.chat.src.agent.workers.query.presentation.transaction_list_plan import (
    build_transaction_list_presentation_plan,
)
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from shared.formatters.currency import format_naira
from shared.formatters.query_transaction_copy import build_transaction_detail_lines
from shared.i18n.message_keys import MessageKey
from shared.i18n.renderer import render_message


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

    return build_transaction_list_presentation_plan(
        result,
        surface_view=surface_view,
        locale=locale,
        current_page=current_page,
        show_expanded=show_expanded,
        has_more=has_more,
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
        from apps.chat.src.agent.workers.query.services.answers.fact_no_results import build_fact_no_results_text

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
    summary_parts = parse_summary_parts(result.summary_text)

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
                        "amount": format_query_amount(item.amount or 0.0),
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
                        "amount": format_query_amount(item.amount or 0.0),
                        "name": item.label if group_by == "account" else item.label.replace("_", " ").title(),
                        "percentage": format_query_percentage(item.amount or 0.0, total_abs),
                        "count": item.count or int(item.metadata.get("count", 0)),
                    },
                )
                for item in surface_view.items
            ],
            hint_text=render_message("query.format.total_line", locale, {"total": format_naira(total_abs)}),
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
        selection_payloads=[],
    )


def _build_single_item_detail_presentation_plan(result: QueryResult, *, locale: str) -> PresentationPlan | None:
    if not result.items or len(result.items) != 1:
        return None

    item = result.items[0]
    query_contract = result_query_contract(result)
    primary_text: str | None = None

    if query_contract is not None and query_contract.answer_fact_field is not None:
        from apps.chat.src.agent.workers.query.services.answers.fact_answer import build_direct_fact_answer

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

    selection_payloads = [surface_item.payload for surface_item in result.surface_view.items] if result.surface_view else []
    return PresentationPlan(
        mode=PresentationMode.DIRECT_ANSWER,
        heading=primary_text or f"*{title}*",
        lead_text=f"*{title}*" if primary_text else None,
        items=items,
        hint_text=hint_text,
        selection_payloads=selection_payloads,
    )


def _is_ranked_transaction_surface(result: QueryResult, *, surface_view: SurfaceView) -> bool:
    if surface_view.mode != SurfaceViewMode.TRANSACTION_LIST:
        return False
    if isinstance(surface_view.context, dict) and surface_view.context.get("type") in ("largest", "smallest"):
        return True
    return any(item.metadata and item.metadata.get("rank") for item in result.items or [])
