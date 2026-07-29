"""Typed query surface builders and selection payload helpers."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from banking.transactions.query.contracts import (
    FactCapability,
    FocusedReferent,
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
)
from banking.transactions.query.models.domain import (
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryFactField,
    QueryIntent,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    NamedAccount,
    NamedCounterparty,
    QueryRequest,
    ResolvedPeriod,
    RetrieveOperation,
    RetrieveProjection,
    RetrieveSelection,
)

ALL_FACT_CAPABILITIES: tuple[FactCapability, ...] = (
    "date",
    "amount",
    "bank",
    "counterparty",
    "status",
    "description",
    "reference",
    "account",
    "direction",
    "category",
)


def build_query_transfer_handoff_payload(item: QueryResultItem) -> dict[str, Any] | None:
    """Build transfer handoff payload from a transaction-like result item."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    recipient_name = str(
        metadata.get("recipient_name") or metadata.get("counterparty") or item.description or ""
    ).strip()
    recipient_account = str(metadata.get("recipient_account_number") or metadata.get("recipient_account") or "").strip()
    recipient_bank_name = str(metadata.get("recipient_bank_name") or metadata.get("bank_name") or "").strip()
    recipient_bank_code = str(metadata.get("recipient_bank_code") or "").strip()
    recipient_bank_code_provider = str(metadata.get("recipient_bank_code_provider") or "").strip().lower()
    recipient_resolution_provider = str(metadata.get("recipient_resolution_provider") or "").strip().lower()
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
        "skip_extraction": True,
    }
    if recipient_bank_code and recipient_bank_code_provider:
        payload["recipient_bank_code"] = recipient_bank_code
        payload["recipient_bank_code_provider"] = recipient_bank_code_provider
        payload["recipient_resolution_provider"] = recipient_resolution_provider or recipient_bank_code_provider
    return {key: value for key, value in payload.items() if value is not None and value != ""}


def build_focus_referent(item: QueryResultItem, *, query_request: QueryRequest | None) -> FocusedReferent | None:
    """Build a shared focused referent from a transaction answer."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    recipient_name = str(metadata.get("recipient_name") or metadata.get("counterparty") or "").strip() or None
    label = recipient_name or _first_filter_value(
        query_request.filters.counterparty if query_request and query_request.filters else None
    )
    if not label:
        return None
    handoff_payload = build_query_transfer_handoff_payload(item)
    selection_payload = SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id=item.id,
        label=label,
        fact_capabilities=list(ALL_FACT_CAPABILITIES),
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
        recipient_bank_code=(
            str(metadata.get("recipient_bank_code") or "").strip()
            if str(metadata.get("recipient_bank_code_provider") or "").strip()
            else None
        )
        or None,
        recipient_resolved_name=str(metadata.get("recipient_resolved_name") or recipient_name or label).strip() or None,
    )


def build_surface_view(result: QueryResult) -> SurfaceView | None:
    """Build a typed surface view from the current query result."""
    if result.surface_view is not None:
        return result.surface_view

    query_request = result_query_request(result)

    if result.answer_strategy == QueryAnswerStrategy.DIRECT_ANSWER and result.answer_context is not None:
        if query_request is not None and _is_summary_scope_direct_answer(query_request):
            return _build_summary_scope_surface_view(
                result,
                answer_context=result.answer_context,
                query_request=query_request,
            )
        direct_items: list[SurfaceItemView] = []
        context: dict[str, Any] = {"hint_text": result.answer_context.hint_text}
        result_items = result.items or []
        if len(result_items) == 1:
            item = result_items[0]
            base_context = build_surface_view_context(result=result, mode=SurfaceViewMode.DIRECT_ANSWER)
            focus_type = "transaction"
            if query_request and query_request.aggregation and query_request.aggregation.group_by:
                focus_type = "account" if query_request.aggregation.group_by == "account" else "group_bucket"
            item_context = _focused_context(
                base=base_context,
                focus_type=focus_type,
            )
            payload = _build_selection_payload(
                item,
                mode=SurfaceViewMode.DIRECT_ANSWER,
                context=item_context,
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
                    "focus_type": "transaction",
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

    if result.answer_strategy == QueryAnswerStrategy.INSIGHT:
        return SurfaceView(
            mode=SurfaceViewMode.INSIGHT,
            lead_text=result.summary_text,
            items=[
                SurfaceItemView(
                    id=item.id,
                    label=item.description,
                    amount=item.amount,
                    metadata=item.metadata or {},
                    payload=SelectionPayload(**cast(dict[str, Any], item.metadata.get("selection_payload")))
                    if isinstance(item.metadata, dict) and item.metadata.get("selection_payload")
                    else _build_selection_payload(
                        item,
                        mode=SurfaceViewMode.INSIGHT,
                        context={"view": "insight"},
                    ),
                )
                for item in result.items or []
            ],
            context={"view": "insight"},
        )

    if (
        result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
        and query_request is not None
        and query_request.intent == QueryIntent.BENEFICIARY_SUMMARY
        and query_request.result_limit == 1
        and result.items
    ):
        context = _focused_context(
            base=build_surface_view_context(result=result, mode=SurfaceViewMode.GROUPED_SUMMARY),
            focus_type="beneficiary",
        )
        item = result.items[0]
        payload = _build_selection_payload(
            item,
            mode=SurfaceViewMode.DIRECT_ANSWER,
            context=context,
        )
        return SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=[
                SurfaceItemView(
                    id=item.id,
                    label=item.description,
                    amount=item.amount,
                    count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
                    payload=payload,
                    metadata=item.metadata or {},
                )
            ],
            lead_text=result.summary_text,
            context={
                **context,
                "selected_payload": payload.model_dump(mode="json"),
                "selected_item_id": item.id,
            },
        )

    if (
        result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
        and query_request is not None
        and query_request.aggregation is not None
        and query_request.aggregation.type in {"largest", "smallest"}
        and result.items
    ):
        if len(result.items) == 1 or query_request.result_limit == 1:
            context = _focused_context(
                base={
                    **build_surface_view_context(result=result, mode=SurfaceViewMode.TRANSACTION_LIST),
                    "ranked_type": query_request.aggregation.type,
                },
                focus_type="transaction",
            )
            item = result.items[0]
            payload = _build_selection_payload(
                item,
                mode=SurfaceViewMode.DIRECT_ANSWER,
                context=context,
            )
            return SurfaceView(
                mode=SurfaceViewMode.DIRECT_ANSWER,
                items=[
                    SurfaceItemView(
                        id=item.id,
                        label=item.description,
                        amount=item.amount,
                        count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
                        payload=payload,
                        metadata=item.metadata or {},
                    )
                ],
                context={
                    **context,
                    "selected_payload": payload.model_dump(mode="json"),
                    "selected_item_id": item.id,
                },
            )

        ranking_context = {
            **build_surface_view_context(result=result, mode=SurfaceViewMode.TRANSACTION_LIST),
            "type": query_request.aggregation.type,
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

    if (
        result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
        and query_request is not None
        and query_request.aggregation is not None
        and (query_request.aggregation.group_by is not None or query_request.intent == QueryIntent.BENEFICIARY_SUMMARY)
        and query_request.result_limit == 1
        and result.items
    ):
        if query_request.intent == QueryIntent.BENEFICIARY_SUMMARY:
            focus_type = "beneficiary"
        else:
            focus_type = "account" if query_request.aggregation.group_by == "account" else "group_bucket"
        context = _focused_context(
            base=build_surface_view_context(result=result, mode=SurfaceViewMode.GROUPED_SUMMARY),
            focus_type=focus_type,
        )
        item = result.items[0]
        payload = _build_selection_payload(
            item,
            mode=SurfaceViewMode.DIRECT_ANSWER,
            context=context,
        )
        return SurfaceView(
            mode=SurfaceViewMode.DIRECT_ANSWER,
            items=[
                SurfaceItemView(
                    id=item.id,
                    label=item.description,
                    amount=item.amount,
                    count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
                    payload=payload,
                    metadata=item.metadata or {},
                )
            ],
            context={
                **context,
                "selected_payload": payload.model_dump(mode="json"),
                "selected_item_id": item.id,
            },
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
    if len(result.items) == 1:
        context["single_item"] = True

    items = [
        SurfaceItemView(
            id=item.id,
            label=item.description,
            amount=item.amount,
            count=(item.metadata or {}).get("count") if isinstance(item.metadata, dict) else None,
            payload=_build_selection_payload(item, mode=mode, context=context),
            metadata=item.metadata or {},
        )
        for item in result.items
    ]
    return SurfaceView(mode=mode, items=items, context=context)


def _is_summary_scope_direct_answer(query_request: QueryRequest | None) -> bool:
    if query_request is None:
        return False
    if query_request.answer_fact_field is not None:
        return False
    if query_request.intent in {
        QueryIntent.ANALYTICS_SUMMARY,
        QueryIntent.CASH_FLOW_SUMMARY,
        QueryIntent.TIME_COMPARISON,
        QueryIntent.AFFORDABILITY,
    }:
        return True
    return False


def _build_summary_scope_surface_view(
    result: QueryResult,
    *,
    answer_context: QueryAnswerContext,
    query_request: QueryRequest,
) -> SurfaceView:
    context = _focused_context(
        base=build_surface_view_context(result=result, mode=SurfaceViewMode.DIRECT_ANSWER),
        focus_type="summary_scope",
    )
    context.update(
        {
            "type": "summary_scope",
            "summary_intent": query_request.intent.value,
            "summary_filters": query_request.filters.model_dump(mode="json")
            if query_request.filters is not None
            else None,
            "summary_aggregation": query_request.aggregation.model_dump(mode="json")
            if query_request.aggregation is not None
            else None,
            "summary_time_range": query_request.time_range.model_dump(mode="json")
            if query_request.time_range is not None
            else {"start": query_request.time_start.isoformat(), "end": query_request.time_end.isoformat()},
            "supported_followups": [
                "show_evidence",
                "breakdown",
                "filter_delta",
                "time_delta",
                "cashflow_compare",
            ],
        }
    )
    label = answer_context.primary_text or result.summary_text or "Summary"
    payload = SelectionPayload(
        selection_kind="summary_scope",
        entity_type="summary_scope",
        entity_id="summary_scope",
        label=label,
        filters_patch={},
        fact_capabilities=[],
    )
    context["selected_payload"] = payload.model_dump(mode="json")
    context["selected_item_id"] = "summary_scope"
    return SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="summary_scope",
                label=label,
                payload=payload,
                metadata={
                    "surface_mode": SurfaceViewMode.DIRECT_ANSWER.value,
                    "focus_type": "summary_scope",
                    "summary_intent": query_request.intent.value,
                },
            )
        ],
        lead_text=answer_context.primary_text,
        context=context,
    )


def _focused_context(*, base: dict[str, Any], focus_type: str) -> dict[str, Any]:
    context = dict(base)
    context["focus_type"] = focus_type
    context.setdefault("type", f"focused_{focus_type}")
    context["single_item"] = True
    return context


def apply_selection_payload_to_query(
    query_request: QueryRequest,
    payload: SelectionPayload,
    *,
    fact_field: QueryFactField | None = None
) -> QueryRequest:
    """Compile a new transaction-list contract from a typed selection payload."""
    if payload.insight_evidence is not None:
        evidence = payload.insight_evidence
        if not isinstance(query_request.operation, AnalyzeOperation):
            raise ValueError("insight evidence must be replayed from its source insight contract")
        if query_request.operation.analysis.insight_type != evidence.insight_type:
            raise ValueError("insight evidence does not match its source insight contract")
        scope = query_request.scope
        if scope is None:
            raise ValueError("insight evidence requires a transaction-backed scope")
        current_start = getattr(evidence, "current_start", None)
        current_end = getattr(evidence, "current_end", None)
        if current_start and current_end:
            period = ResolvedPeriod(
                start=date.fromisoformat(current_start),
                end=date.fromisoformat(current_end)
            )
        else:
            period = scope.period

        analysis = query_request.operation.analysis.model_copy(deep=True)
        analysis = analysis.model_copy(update={"evidence": evidence})
        return QueryRequest(
            operation=AnalyzeOperation(scope=scope.model_copy(update={"period": period}), analysis=analysis)
        )

    scope = query_request.scope
    if scope is None:
        raise ValueError("selection requires a transaction-backed scope")
    predicate = scope.predicate.model_copy(deep=True)
    accounts = scope.accounts
    counterparties = payload.filters_patch.get("counterparty")
    if isinstance(counterparties, list) and counterparties:
        from banking.transactions.query.models.operations import CounterpartySelector

        predicate.counterparty = CounterpartySelector(
            role="any", reference=NamedCounterparty(name=str(counterparties[0]))
        )
    for field, target in (("transaction_type", "direction"), ("category", "categories"), ("status", "statuses")):
        if field in payload.filters_patch:
            value = payload.filters_patch[field]
            setattr(predicate, target, [value] if target.endswith("s") and not isinstance(value, list) else value)
    account_filter = payload.filters_patch.get("account_filter")
    if isinstance(account_filter, str) and account_filter.strip():
        accounts = NamedAccount(name=account_filter.strip())
    period = scope.period
    if payload.time_patch:
        start_raw = payload.time_patch.get("start")
        end_raw = payload.time_patch.get("end")
        if isinstance(start_raw, str) and isinstance(end_raw, str):
            period = ResolvedPeriod.model_validate(
                {
                    "start": start_raw,
                    "end": end_raw,
                    "granularity": payload.time_patch.get("granularity"),
                }
            )

    return QueryRequest(
        operation=RetrieveOperation(
            scope=scope.model_copy(update={"period": period, "predicate": predicate, "accounts": accounts}),
            projection=RetrieveProjection(shape="fact" if fact_field else "list", fact_field=fact_field),
            selection=RetrieveSelection(cardinality="one" if fact_field else "many"),
        )
    )


def result_query_request(result: QueryResult) -> QueryRequest | None:
    """Read the query execution contract from the runtime result."""
    return result.query_request


def build_surface_view_context(*, result: QueryResult, mode: SurfaceViewMode) -> dict[str, Any]:
    query_request = result_query_request(result)
    context: dict[str, Any] = {"mode": mode.value}
    if mode == SurfaceViewMode.GROUPED_SUMMARY:
        if query_request and query_request.intent == QueryIntent.BENEFICIARY_SUMMARY:
            context["view"] = "beneficiary_summary"
        elif query_request and query_request.aggregation and query_request.aggregation.group_by:
            context["group_by"] = query_request.aggregation.group_by
            context["surface_type"] = "breakdown"
        elif query_request and query_request.intent in {
            QueryIntent.ANALYTICS_SUMMARY,
            QueryIntent.TIME_COMPARISON,
            QueryIntent.AFFORDABILITY,
        }:
            context["view"] = "summary"
    elif mode == SurfaceViewMode.TRANSACTION_LIST:
        context["type"] = "transaction_list"
    return context


def _build_selection_payload(
    item: QueryResultItem,
    *,
    mode: SurfaceViewMode,
    context: dict[str, Any],
) -> SelectionPayload:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    handoff_payload = build_query_transfer_handoff_payload(item)

    group_by = str(context.get("group_by") or "").strip()
    if mode in {SurfaceViewMode.GROUPED_SUMMARY, SurfaceViewMode.DIRECT_ANSWER} and group_by:
        group_key = str(metadata.get("key") or item.description).strip()
        filters_patch: dict[str, Any] = {}
        time_patch: dict[str, Any] | None = None
        selection_kind = "group_bucket"
        entity_type = "group_bucket"
        if group_by == "account":
            filters_patch["account_filter"] = group_key
            if context.get("focus_type") == "account":
                selection_kind = "account"
                entity_type = "account"
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
            selection_kind=cast(Any, selection_kind),
            entity_type=entity_type,
            entity_id=item.id,
            label=item.description,
            group_by=cast(Any, group_by or None),
            group_key=group_key,
            filters_patch=filters_patch,
            time_patch=time_patch,
            fact_capabilities=list(ALL_FACT_CAPABILITIES),
        )

    is_beneficiary_summary = context.get("view") == "beneficiary_summary" and mode in {
        SurfaceViewMode.GROUPED_SUMMARY,
        SurfaceViewMode.DIRECT_ANSWER,
    }
    if is_beneficiary_summary:
        recipient_name = str(metadata.get("recipient_name") or item.description).strip()
        return SelectionPayload(
            selection_kind="beneficiary",
            entity_type="beneficiary",
            entity_id=item.id,
            label=recipient_name,
            filters_patch={"counterparty": [recipient_name]},
            fact_capabilities=list(ALL_FACT_CAPABILITIES),
        )

    return SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id=item.id,
        label=item.description,
        fact_capabilities=list(ALL_FACT_CAPABILITIES),
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
