"""Typed query surface builders and selection payload helpers."""

from __future__ import annotations

from typing import Any, Literal, cast

from apps.core.src.agent.graphs.query.models import (
    Filters,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryIR,
    QueryOperation,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.core.src.agent.shared.query_contracts import (
    FocusedReferent,
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


def build_focus_referent(item: QueryResultItem, *, query_contract: QueryExecutionContract | None) -> FocusedReferent | None:
    """Build a shared focused referent from a transaction answer."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    recipient_name = str(metadata.get("recipient_name") or metadata.get("counterparty") or "").strip() or None
    label = recipient_name or _first_filter_value(
        query_contract.filters.counterparty if query_contract and query_contract.filters else None
    )
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

    query_contract = result_query_contract(result)

    if result.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER and result.answer_context is not None:
        direct_items: list[SurfaceItemView] = []
        context: dict[str, Any] = {"hint_text": result.answer_context.hint_text}
        result_items = result.items or []
        if len(result_items) == 1:
            item = result_items[0]
            payload = _build_selection_payload(
                result,
                item,
                mode=SurfaceViewMode.DIRECT_ANSWER,
                context={"type": "single_transaction"},
            )
            direct_items = [
                SurfaceItemView(
                    id=item.id,
                    label=item.description,
                    amount=item.amount,
                    payload=payload,
                    metadata=item.metadata or {},
                )
            ]
            context.update(
                {
                    "type": "single_transaction",
                    "selected_payload": payload.model_dump(mode="json"),
                    "selected_item_id": item.id,
                }
            )
        return SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=direct_items,
            lead_text=result.answer_context.primary_text,
            context=context,
        )
    if result.answer_strategy == QueryAnswerStrategy.CLARIFY and result.answer_context is not None:
        return SurfaceView(
            mode=SurfaceViewMode.CLARIFICATION,
            lead_text=result.answer_context.primary_text,
        )

    if (
        result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
        and query_contract is not None
        and query_contract.aggregation is not None
        and query_contract.aggregation.type in {"largest", "smallest"}
        and result.items
    ):
        ranking_context = {
            **build_surface_view_context(result=result, mode=SurfaceViewMode.TRANSACTION_LIST),
            "type": query_contract.aggregation.type,
        }
        return SurfaceView(
            mode=SurfaceViewMode.TRANSACTION_LIST,
            items=[
                SurfaceItemView(
                    id=item.id,
                    label=item.description,
                    amount=item.amount,
                    count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
                    payload=_build_selection_payload(
                        result,
                        item,
                        mode=SurfaceViewMode.TRANSACTION_LIST,
                        context=ranking_context,
                    ),
                    metadata=item.metadata or {},
                )
                for item in result.items or []
            ],
            context=ranking_context,
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
                        context=build_surface_view_context(result=result, mode=SurfaceViewMode.GROUPED_SUMMARY),
                    ),
                    metadata=item.metadata or {},
                )
                for item in result.items or []
            ],
            context=build_surface_view_context(result=result, mode=SurfaceViewMode.GROUPED_SUMMARY),
        )

    if not result.items:
        return None

    mode = (
        SurfaceViewMode.GROUPED_SUMMARY
        if result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
        else SurfaceViewMode.TRANSACTION_LIST
    )
    context = build_surface_view_context(result=result, mode=mode)

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


def apply_selection_payload_to_query(
    query_contract: QueryExecutionContract,
    payload: SelectionPayload,
    *,
    fact_field: Literal["date", "counterparty", "amount", "bank"] | None = None,
    continuation_type: str | None = None,
    continuation_delta_type: str | None = None,
) -> QueryExecutionContract:
    """Compile a new transaction-list contract from a typed selection payload."""
    filters = query_contract.filters.model_copy(deep=True) if query_contract.filters is not None else Filters()
    for key, value in payload.filters_patch.items():
        setattr(filters, key, value)

    time_range = query_contract.time_range or TimeRange(start=query_contract.time_start, end=query_contract.time_end)
    if payload.time_patch:
        start_raw = payload.time_patch.get("start")
        end_raw = payload.time_patch.get("end")
        if isinstance(start_raw, str) and isinstance(end_raw, str):
            time_range = TimeRange.model_validate(
                {
                    "start": start_raw,
                    "end": end_raw,
                    "granularity": payload.time_patch.get("granularity"),
                }
            )

    return QueryExecutionContract.from_query_ir(
        QueryIR(
            intent=QueryIntent.TRANSACTION_LIST,
            query_operation=QueryOperation.LIST_TRANSACTIONS,
            timezone=query_contract.timezone,
            time_range=time_range,
            filters=filters,
            aggregation=None,
            accounts_scope=query_contract.accounts_scope,
            account_name=query_contract.account_name,
            amount_check=query_contract.amount_check,
            item_name=query_contract.item_name,
            analysis_type=query_contract.analysis_type,
            result_limit=None,
            result_reference=None,
            answer_fact_field=fact_field,
            comparison=query_contract.comparison.model_copy(deep=True) if query_contract.comparison is not None else None,
            continuation_type=continuation_type,
            continuation_delta_type=continuation_delta_type,
            intent_spec=query_contract.intent_spec.model_copy(deep=True) if query_contract.intent_spec is not None else None,
        )
    )


def result_query_contract(result: QueryResult) -> QueryExecutionContract | None:
    """Read the query execution contract from the runtime result."""
    return result.query_contract


def build_surface_view_context(*, result: QueryResult, mode: SurfaceViewMode) -> dict[str, Any]:
    query_contract = result_query_contract(result)
    context: dict[str, Any] = {"mode": mode.value}
    if mode == SurfaceViewMode.GROUPED_SUMMARY:
        if query_contract and query_contract.intent == QueryIntent.BENEFICIARY_SUMMARY:
            context["view"] = "beneficiary_summary"
        elif query_contract and query_contract.aggregation and query_contract.aggregation.group_by:
            context["group_by"] = query_contract.aggregation.group_by
            context["surface_type"] = "breakdown"
        elif query_contract and query_contract.intent in {
            QueryIntent.ANALYTICS_SUMMARY,
            QueryIntent.TIME_COMPARISON,
            QueryIntent.AFFORDABILITY,
        }:
            context["view"] = "summary"
    elif mode == SurfaceViewMode.TRANSACTION_LIST:
        context["type"] = "transaction_list"
    return context


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
