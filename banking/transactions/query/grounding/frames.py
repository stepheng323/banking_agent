"""Query frame lifecycle and grounded contract reconstruction."""

from __future__ import annotations

from calendar import monthrange
from typing import Any, SupportsFloat, SupportsInt, cast

from banking.transactions.query.continuations.transforms import rebuild_query_contract
from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import (
    ComparisonDirective,
    QueryExecutionContract,
    QueryFrame,
    QueryFrameFacts,
    QueryIntent,
    QueryResult,
    TimeRange,
)

MAX_QUERY_FRAMES = 3


def restore_query_frames(raw_frames: object) -> list[QueryFrame]:
    """Restore compact query frames from session storage."""
    if not isinstance(raw_frames, list):
        return []

    restored: list[QueryFrame] = []
    for raw_frame in raw_frames:
        try:
            frame = raw_frame if isinstance(raw_frame, QueryFrame) else QueryFrame.model_validate(raw_frame)
        except Exception:
            continue
        restored.append(frame)
    return restored


def append_query_frame(
    existing_frames: list[QueryFrame],
    *,
    query_contract: QueryExecutionContract,
    result: QueryResult,
    max_frames: int = MAX_QUERY_FRAMES,
) -> list[QueryFrame]:
    """Append a compact frame for the latest executed query and trim history."""
    next_turn_index = max((frame.turn_index for frame in existing_frames), default=0) + 1
    frame = build_query_frame(
        query_contract=query_contract,
        result=result,
        turn_index=next_turn_index,
    )
    return [*existing_frames, frame][-max_frames:]


def build_query_frame(
    *,
    query_contract: QueryExecutionContract,
    result: QueryResult,
    turn_index: int,
) -> QueryFrame:
    """Build a compact query frame from a query execution result."""
    surface_view = result.surface_view
    facts = _derive_query_frame_facts(query_contract=query_contract, result=result, surface_view=surface_view)

    return QueryFrame(
        frame_id=f"qf_{turn_index}",
        turn_index=turn_index,
        query_contract=query_contract,
        summary_text=result.summary_text,
        interpretation=result.interpretation,
        surface_type=surface_view.mode if surface_view else None,
        surface_context=surface_view.context if surface_view else {},
        visible_items=_visible_item_snapshots(surface_view),
        facts=facts,
    )


def resolve_query_frames(query_frames: list[QueryFrame], frame_ids: list[str] | None) -> list[QueryFrame]:
    """Resolve selected frames in the order chosen by the semantic reasoner."""
    if not frame_ids:
        return []

    frame_map = {frame.frame_id: frame for frame in query_frames}
    return [frame_map[frame_id] for frame_id in frame_ids if frame_id in frame_map]


def _visible_item_snapshots(surface_view: SurfaceView | None) -> list[dict[str, Any]]:
    if surface_view is None:
        return []

    snapshots: list[dict[str, Any]] = []
    page_start = surface_view.context.get("start_index") if isinstance(surface_view.context, dict) else None
    for idx, item in enumerate(surface_view.items, 1):
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        snapshots.append(
            {
                "id": item.id,
                "label": item.label,
                "amount": item.amount,
                "date": metadata.get("date"),
                "bank": metadata.get("bank_name") or metadata.get("recipient_bank_name"),
                "counterparty": metadata.get("counterparty") or metadata.get("recipient_name"),
                "status": metadata.get("status"),
                "direction": metadata.get("direction") or metadata.get("transaction_type") or metadata.get("type"),
                "page_position": idx,
                "absolute_position": (int(page_start) + idx - 1) if isinstance(page_start, int) else None,
            }
        )
    return snapshots


def build_grounded_query_contract(
    *,
    query_frames: list[QueryFrame],
    frame_ids: list[str] | None,
    operation: str | None,
) -> QueryExecutionContract | None:
    """Compile a grounded query contract from selected prior frames."""
    frames = resolve_query_frames(query_frames, frame_ids)
    if not frames:
        return None

    if operation in {"select_frame", "reuse_frame"}:
        return frames[0].query_contract.model_copy(deep=True)

    if operation == "show_transactions":
        return rebuild_query_contract(
            frames[0].query_contract,
            intent=QueryIntent.TRANSACTION_LIST,
            aggregation=None,
            result_limit=None,
            result_reference=None,
        )

    if operation == "compare_frames":
        if len(frames) < 2 or not can_compare_frames(frames[0], frames[1]):
            return None

        current_frame, comparison_frame = frames[0], frames[1]
        comparison_range = comparison_frame.query_contract.time_range
        if comparison_range is None or current_frame.query_contract.time_range is None:
            return None

        return rebuild_query_contract(
            current_frame.query_contract,
            intent=QueryIntent.TIME_COMPARISON,
            comparison=ComparisonDirective(mode="explicit_range", explicit_range=comparison_range),
        )

    return None


def _derive_query_frame_facts(
    *,
    query_contract: QueryExecutionContract,
    result: QueryResult,
    surface_view: SurfaceView | None,
) -> QueryFrameFacts:
    facts = QueryFrameFacts(
        label=result.summary_text,
        direction=query_contract.filters.transaction_type if query_contract.filters else None,
    )
    aggregation_type = query_contract.aggregation.type if query_contract.aggregation else None

    if query_contract.intent == QueryIntent.ANALYTICS_SUMMARY and aggregation_type == "sum":
        return facts.model_copy(
            update={
                "metric_kind": "amount",
                "amount": sum(item.amount for item in result.items or []),
                "count": len(result.items or []),
            }
        )

    if query_contract.intent == QueryIntent.ANALYTICS_SUMMARY and aggregation_type == "average":
        average_amount = None
        if result.items:
            average_amount = sum(item.amount for item in result.items) / len(result.items)
        return facts.model_copy(
            update={
                "metric_kind": "average",
                "amount": average_amount,
                "count": len(result.items or []),
            }
        )

    if query_contract.intent == QueryIntent.ANALYTICS_SUMMARY and aggregation_type == "count":
        return facts.model_copy(
            update={
                "metric_kind": "count",
                "count": len(result.items or []),
            }
        )

    if query_contract.intent == QueryIntent.TIME_COMPARISON:
        amount, comparison_amount, count = _extract_time_comparison_facts(result)
        return facts.model_copy(
            update={
                "metric_kind": "comparison",
                "amount": amount,
                "comparison_amount": comparison_amount,
                "count": count,
            }
        )

    if query_contract.intent == QueryIntent.TRANSACTION_LIST:
        return facts.model_copy(update={"metric_kind": "transactions", "count": len(result.items or [])})

    if query_contract.intent == QueryIntent.TRANSACTION_SEARCH:
        return facts.model_copy(update={"metric_kind": "single_item", "count": len(result.items or [])})

    if surface_view and surface_view.mode == SurfaceViewMode.TRANSACTION_LIST:
        return facts.model_copy(update={"metric_kind": "ranked", "count": len(result.items or [])})

    return facts


def _extract_time_comparison_facts(result: QueryResult) -> tuple[float | None, float | None, int | None]:
    amount: float | None = None
    comparison_amount: float | None = None
    count: int | None = None

    for item in result.items or []:
        metadata = item.metadata or {}
        if item.id == "spending":
            amount = _coerce_float(metadata.get("current"))
            comparison_amount = _coerce_float(metadata.get("comparison"))
        if item.id == "count":
            count = _coerce_int(metadata.get("current"))

    return amount, comparison_amount, count


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(cast(SupportsFloat | str, value))
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(cast(SupportsInt | str | bytes | bytearray, value))
    except (TypeError, ValueError):
        return None


def can_compare_frames(current_frame: QueryFrame, comparison_frame: QueryFrame) -> bool:
    current_query = current_frame.query_contract
    comparison_query = comparison_frame.query_contract
    if (
        current_query.intent != QueryIntent.ANALYTICS_SUMMARY
        or comparison_query.intent != QueryIntent.ANALYTICS_SUMMARY
    ):
        return False

    current_agg = current_query.aggregation.type if current_query.aggregation else None
    comparison_agg = comparison_query.aggregation.type if comparison_query.aggregation else None
    if current_agg != "sum" or comparison_agg != "sum":
        return False

    return _frame_shape_signature(current_frame) == _frame_shape_signature(comparison_frame)


def _frame_shape_signature(frame: QueryFrame) -> dict[str, Any]:
    query = frame.query_contract
    filters = query.filters.model_dump(exclude_none=True) if query.filters else {}
    aggregation = query.aggregation.model_dump(exclude_none=True) if query.aggregation else {}
    return {
        "intent": query.intent.value,
        "filters": filters,
        "aggregation": aggregation,
        "accounts_scope": query.accounts_scope,
        "account_name": query.account_name,
        "result_limit": query.result_limit,
        "result_reference": query.result_reference,
    }


def format_period_label(period: TimeRange | None) -> str | None:
    if period is None:
        return None

    is_same_month = period.start.month == period.end.month and period.start.year == period.end.year
    if is_same_month and _is_full_month_window(period):
        return period.start.strftime("%B %Y")
    if period.start.year == period.end.year:
        return f"{period.start.strftime('%b %d')} - {period.end.strftime('%b %d')}"
    return f"{period.start.strftime('%b %d, %Y')} - {period.end.strftime('%b %d, %Y')}"


def _is_full_month_window(period: TimeRange) -> bool:
    if period.start.year != period.end.year or period.start.month != period.end.month:
        return False
    return period.start.day == 1 and period.end.day == monthrange(period.start.year, period.start.month)[1]
