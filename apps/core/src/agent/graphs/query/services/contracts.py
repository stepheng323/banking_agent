"""Typed query surface and presentation contracts."""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryAnswerStrategy,
    QueryIntent,
    QueryOperation,
    QueryResult,
    QueryResultItem,
    SurfaceType,
    TimeRange,
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

    if not result.items:
        return None

    surface_type = result.surface.type if result.surface is not None else None
    if surface_type in {SurfaceType.SUMMARY, SurfaceType.BREAKDOWN}:
        mode = SurfaceViewMode.GROUPED_SUMMARY
    else:
        mode = SurfaceViewMode.TRANSACTION_LIST

    items = [
        SurfaceItemView(
            id=item.id,
            label=item.description,
            amount=item.amount,
            count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
            payload=_build_selection_payload(result, item, surface_type=surface_type),
            metadata=item.metadata or {},
        )
        for item in result.items
    ]

    context = {}
    if result.surface is not None:
        context = dict(result.surface.context or {})
        if result.surface.type is not None:
            context["surface_type"] = result.surface.type.value
    return SurfaceView(mode=mode, items=items, context=context)


def build_presentation_plan(result: QueryResult, *, locale: str = "en") -> PresentationPlan | None:
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

    if surface_view.mode == SurfaceViewMode.GROUPED_SUMMARY:
        return PresentationPlan(
            mode=PresentationMode.SUMMARY_LIST,
            heading=result.summary_text,
            items=[],
            selection_payloads=[item.payload for item in surface_view.items],
        )

    return PresentationPlan(
        mode=PresentationMode.TRANSACTION_LIST,
        heading=result.summary_text,
        items=[],
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
    surface_type: SurfaceType | None,
) -> SelectionPayload:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    handoff_payload = build_query_transfer_handoff_payload(item)

    if surface_type == SurfaceType.BREAKDOWN:
        group_by = str((result.surface.context or {}).get("group_by") or "").strip() if result.surface else ""
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

    is_beneficiary_summary = bool(
        result.surface is not None
        and result.surface.type == SurfaceType.SUMMARY
        and isinstance(result.surface.context, dict)
        and result.surface.context.get("view") == "beneficiary_summary"
    )
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
