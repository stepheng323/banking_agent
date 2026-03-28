"""Typed query surface and presentation contracts."""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date, datetime
from typing import Any, Literal, cast

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryAnswerStrategy,
    QueryIntent,
    QueryOperation,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.presentation_scope import (
    build_breakdown_heading,
    build_transaction_heading,
)
from apps.core.src.agent.shared.query_contracts import (
    FocusedReferent,
    PresentationMode,
    PresentationPlan,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from shared.i18n import render_message


def build_query_transfer_handoff_payload(item: QueryResultItem) -> dict[str, Any] | None:
    """Build transfer handoff payload from a transaction-like result item."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    recipient_name = str(metadata.get("recipient_name") or metadata.get("counterparty") or item.description or "").strip()
    recipient_account = str(
        metadata.get("recipient_account_number") or metadata.get("recipient_account") or ""
    ).strip()
    recipient_bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip()
    recipient_bank_code = str(metadata.get("recipient_bank_code") or "").strip()
    tx_type = str(metadata.get("transaction_type") or metadata.get("type") or "").strip().lower()
    if not recipient_name:
        return None
    is_transfer_like = tx_type == "transfer" or bool(recipient_account and (recipient_bank_name or recipient_bank_code))
    if not is_transfer_like:
        return None
    payload: dict[str, Any] = {
        "action": "send_money",
        "amount": item.amount,
        "narration": item.description,
        "recipient_name": recipient_name,
        "recipient_account": recipient_account or None,
        "recipient_bank_name": recipient_bank_name or None,
        "recipient_bank_code": recipient_bank_code or None,
        "skip_extraction": True,
    }
    return {key: value for key, value in payload.items() if value is not None and value != ""}


def build_focus_referent(item: QueryResultItem, *, query: NormalizedQuery | None) -> FocusedReferent | None:
    """Build a shared focused referent from a transaction answer."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    recipient_name = str(metadata.get("recipient_name") or metadata.get("counterparty") or "").strip() or None
    label = recipient_name or _first_filter_value(query.filters.counterparty if query and query.filters else None)
    if not label:
        return None
    handoff_payload = build_query_transfer_handoff_payload(item)
    selection_payload = SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id=item.id,
        label=label,
        fact_capabilities=["date", "amount", "bank", "counterparty"],
        handoff_payload=handoff_payload,
    )
    return FocusedReferent(
        referent_type="beneficiary",
        label=label,
        entity_id=item.id,
        selection_payload=selection_payload,
        recipient_name=recipient_name or label,
        recipient_account=str(
            metadata.get("recipient_account_number") or metadata.get("recipient_account") or ""
        ).strip()
        or None,
        recipient_bank_name=str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip() or None,
        recipient_bank_code=str(metadata.get("recipient_bank_code") or "").strip() or None,
        recipient_resolved_name=str(metadata.get("recipient_resolved_name") or recipient_name or label).strip() or None,
    )


def build_surface_view(result: QueryResult) -> SurfaceView | None:
    """Build a typed surface view from the current query result."""
    if result.surface_view is not None:
        return result.surface_view

    if result.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER and result.answer_context is not None:
        return SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            lead_text=result.answer_context.primary_text,
            context={"hint_text": result.answer_context.hint_text},
        )
    if result.answer_strategy == QueryAnswerStrategy.CLARIFY and result.answer_context is not None:
        return SurfaceView(
            mode=SurfaceViewMode.CLARIFICATION,
            lead_text=result.answer_context.primary_text,
        )

    if result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST:
        return SurfaceView(
            mode=SurfaceViewMode.GROUPED_SUMMARY,
            items=[
                SurfaceItemView(
                    id=item.id,
                    label=item.description,
                    amount=item.amount,
                    count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
                    payload=_build_selection_payload(
                        result,
                        item,
                        mode=SurfaceViewMode.GROUPED_SUMMARY,
                        context=_build_surface_view_context(result=result, mode=SurfaceViewMode.GROUPED_SUMMARY),
                    ),
                    metadata=item.metadata or {},
                )
                for item in result.items or []
            ],
            context=_build_surface_view_context(result=result, mode=SurfaceViewMode.GROUPED_SUMMARY),
        )

    if not result.items:
        return None

    mode = (
        SurfaceViewMode.GROUPED_SUMMARY
        if result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
        else SurfaceViewMode.TRANSACTION_LIST
    )
    context = _build_surface_view_context(result=result, mode=mode)

    items = [
        SurfaceItemView(
            id=item.id,
            label=item.description,
            amount=item.amount,
            count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
            payload=_build_selection_payload(result, item, mode=mode, context=context),
            metadata=item.metadata or {},
        )
        for item in result.items
    ]
    return SurfaceView(mode=mode, items=items, context=context)


def build_presentation_plan(
    result: QueryResult,
    *,
    locale: str = "en",
    current_page: int = 0,
    show_expanded: bool = False,
    has_more: bool = False,
) -> PresentationPlan | None:
    """Build a typed presentation plan from the execution result."""
    if result.presentation_plan is not None:
        return result.presentation_plan

    surface_view = result.surface_view or build_surface_view(result)
    if surface_view is None:
        return None

    if result.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER and result.answer_context is not None:
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


def find_selection_payload(
    surface_view: SurfaceView | None,
    *,
    index: int | None = None,
    label: str | None = None,
) -> SelectionPayload | None:
    """Resolve a selection payload from a typed surface view."""
    if surface_view is None or not surface_view.items:
        return None
    if index is not None and 0 <= index < len(surface_view.items):
        return surface_view.items[index].payload
    if label:
        normalized_label = _normalize_label(label)
        exact = [item.payload for item in surface_view.items if _normalize_label(item.label) == normalized_label]
        if len(exact) == 1:
            return exact[0]

        token_matches: list[tuple[int, SelectionPayload]] = []
        candidate_tokens = set(_tokenize_label(normalized_label))
        for item in surface_view.items:
            item_tokens = set(_tokenize_label(item.label))
            overlap = candidate_tokens & item_tokens
            if overlap:
                token_matches.append((max(len(token) for token in overlap), item.payload))
        if len(token_matches) == 1:
            return token_matches[0][1]
        if len(token_matches) > 1:
            token_matches.sort(key=lambda entry: entry[0], reverse=True)
            if token_matches[0][0] > token_matches[1][0]:
                return token_matches[0][1]
    return None


def apply_selection_payload_to_query(
    query: NormalizedQuery,
    payload: SelectionPayload,
    *,
    fact_field: Literal["date", "counterparty", "amount", "bank"] | None = None,
) -> NormalizedQuery:
    """Compile a new transaction-list query from a typed selection payload."""
    new_query = query.model_copy(deep=True)
    new_query.intent = QueryIntent.TRANSACTION_LIST
    new_query.query_operation = QueryOperation.LIST_TRANSACTIONS
    new_query.aggregation = None
    new_query.result_limit = None
    new_query.result_reference = None
    new_query.answer_fact_field = fact_field

    filters = new_query.filters.model_copy(deep=True) if new_query.filters is not None else Filters()
    for key, value in payload.filters_patch.items():
        setattr(filters, key, value)
    new_query.filters = filters

    if payload.time_patch:
        start_raw = payload.time_patch.get("start")
        end_raw = payload.time_patch.get("end")
        if isinstance(start_raw, str) and isinstance(end_raw, str):
            new_query.time_range = TimeRange.model_validate(
                {
                    "start": start_raw,
                    "end": end_raw,
                    "granularity": payload.time_patch.get("granularity"),
                }
            )

    return new_query


def _build_selection_payload(
    result: QueryResult,
    item: QueryResultItem,
    *,
    mode: SurfaceViewMode,
    context: dict[str, Any],
) -> SelectionPayload:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    handoff_payload = build_query_transfer_handoff_payload(item)

    group_by = str(context.get("group_by") or "").strip()
    if mode == SurfaceViewMode.GROUPED_SUMMARY and group_by:
        group_key = str(metadata.get("key") or item.description).strip()
        filters_patch: dict[str, Any] = {}
        time_patch: dict[str, Any] | None = None
        if group_by == "account":
            filters_patch["account_filter"] = group_key
        elif group_by == "merchant":
            filters_patch["counterparty"] = [group_key]
        elif group_by == "transaction_type":
            tx_type = group_key.lower()
            if tx_type in {"credit", "debit"}:
                filters_patch["transaction_type"] = tx_type
        elif group_by == "day":
            time_patch = {
                "start": item.date.isoformat(),
                "end": item.date.isoformat(),
                "granularity": "day",
            }
        else:
            filters_patch["category"] = [group_key.lower()]
        return SelectionPayload(
            selection_kind="group_bucket",
            entity_type="group_bucket",
            entity_id=item.id,
            label=item.description,
            group_by=cast(Any, group_by or None),
            group_key=group_key,
            filters_patch=filters_patch,
            time_patch=time_patch,
            fact_capabilities=["date", "amount"],
        )

    is_beneficiary_summary = mode == SurfaceViewMode.GROUPED_SUMMARY and context.get("view") == "beneficiary_summary"
    if is_beneficiary_summary:
        recipient_name = str(metadata.get("recipient_name") or item.description).strip()
        return SelectionPayload(
            selection_kind="beneficiary",
            entity_type="beneficiary",
            entity_id=item.id,
            label=recipient_name,
            filters_patch={"counterparty": [recipient_name]},
            fact_capabilities=["date", "amount", "bank", "counterparty"],
        )

    return SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id=item.id,
        label=item.description,
        fact_capabilities=["date", "amount", "bank", "counterparty"],
        handoff_payload=handoff_payload,
    )


def _first_filter_value(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _normalize_label(value: str) -> str:
    return " ".join(re.sub(r"'s\b", "", value.casefold()).split()).rstrip(".,;:!?")


def _tokenize_label(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) >= 3]


def _build_surface_view_context(*, result: QueryResult, mode: SurfaceViewMode) -> dict[str, Any]:
    query = result.query_snapshot
    context: dict[str, Any] = {"mode": mode.value}
    if mode == SurfaceViewMode.GROUPED_SUMMARY:
        if query and query.intent == QueryIntent.BENEFICIARY_SUMMARY:
            context["view"] = "beneficiary_summary"
        elif query and query.aggregation and query.aggregation.group_by:
            context["group_by"] = query.aggregation.group_by
            context["surface_type"] = "breakdown"
        elif query and query.intent in {
            QueryIntent.ANALYTICS_SUMMARY,
            QueryIntent.TIME_COMPARISON,
            QueryIntent.AFFORDABILITY,
        }:
            context["view"] = "summary"
    elif mode == SurfaceViewMode.TRANSACTION_LIST:
        context["type"] = "transaction_list"
    return context


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
                result.query_snapshot,
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
    query_snapshot = result.query_snapshot
    primary_text: str | None = None

    if query_snapshot is not None and query_snapshot.answer_fact_field is not None:
        from apps.core.src.agent.graphs.query.services.answer_strategy import build_direct_fact_answer

        answer_context = build_direct_fact_answer(
            item,
            query=query_snapshot,
            fact_field=query_snapshot.answer_fact_field,
            locale=locale,
        )
        primary_text = answer_context.primary_text

    title = render_message("query.format.transaction_details_title", locale)
    if query_snapshot and query_snapshot.result_reference == "latest":
        title = render_message("query.format.last_transaction_title", locale)
        tx_filters = query_snapshot.filters
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

    if item.id:
        items.append(render_message("query.format.field_ref", locale, {"reference": item.id}))

    hint_text = None
    if transaction_type == "transfer":
        hint_text = render_message("query.format.transfer_reply_hint", locale)

    selection_payloads = [surface_item.payload for surface_item in result.surface_view.items] if result.surface_view else []
    return PresentationPlan(
        mode=PresentationMode.TRANSACTION_LIST,
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

    heading = build_transaction_heading(result.query_snapshot, locale=locale)
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
