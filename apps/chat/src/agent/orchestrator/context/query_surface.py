"""Query surface helpers backed by orchestrator context frames."""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Protocol

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from banking.transactions.query.contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode
from banking.transactions.query.grounding.frames import build_query_frame
from banking.transactions.query.models.domain import (
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryResult,
    QueryResultItem,
)


class QueryContextFrameState(Protocol):
    @property
    def context_frames(self) -> list[ContextFrame]: ...


def query_surface_is_active(frame: ContextFrame, *, now: int | None = None) -> bool:
    """Return whether a context frame can serve as active query surface state."""
    current_time = int(time.time()) if now is None else now
    if frame.created_at_ts + frame.ttl_seconds <= current_time:
        return False
    if not frame.items:
        return False
    return frame.metadata.get("source") == "query" and bool(frame.metadata.get("query_contract"))


def get_active_query_surface(state: QueryContextFrameState, *, now: int | None = None) -> ContextFrame | None:
    """Return the latest active query context frame."""
    frames = getattr(state, "context_frames", [])
    if not isinstance(frames, list):
        return None
    for frame in reversed(frames):
        if not isinstance(frame, ContextFrame):
            continue
        if query_surface_is_active(frame, now=now):
            return frame
    return None


def summarize_query_surface_for_planner(frame: ContextFrame) -> str:
    """Build compact prompt context for the latest query surface."""
    summary_text = str(frame.metadata.get("summary_text") or "").strip()
    lead_text = str(frame.metadata.get("lead_text") or "").strip()
    visible = []
    for idx, item in enumerate(frame.items[:5], 1):
        amount = item.data.get("amount")
        amount_text = f" ({amount})" if amount not in (None, "") else ""
        visible.append(f"[{idx}] {item.label}{amount_text}")
    visible_text = ", ".join(visible)
    if len(frame.items) > len(visible):
        visible_text = f"{visible_text}, ... (+{len(frame.items) - len(visible)} more)"
    last_result = summary_text or lead_text or visible_text
    detail = f' Last result: "{last_result}".' if last_result else ""
    items = f" Visible items: {visible_text}." if visible_text else ""
    return (
        "Active Query Session: The user recently viewed transaction results."
        f"{detail}{items}\n"
        "- Continuation/refinement/fact questions about the displayed query stay in query.\n"
        "- Use the active query surface for references like 'that', 'when', "
        "'reference?', 'show them', and time changes."
    )


def build_query_context_for_worker(state: QueryContextFrameState) -> dict[str, Any]:
    """Build worker input context from the active orchestrator-owned query surface."""
    frame = get_active_query_surface(state)
    if frame is None:
        return {}
    snapshot = build_query_session_snapshot_from_surface(frame, context_frames=getattr(state, "context_frames", []))
    if snapshot is None:
        return {}
    return {
        "active_query_surface": frame.model_dump(mode="json"),
        "active_query_session": snapshot,
    }


def build_query_session_snapshot_from_surface(
    frame: ContextFrame,
    *,
    context_frames: list[ContextFrame] | None = None,
) -> dict[str, Any] | None:
    """Project a context frame into the legacy session shape used by continuation code."""
    contract = _restore_query_contract(frame.metadata.get("query_contract"))
    if contract is None:
        return None

    surface_view = _surface_view_from_frame(frame)
    query_items = [_query_item_from_surface_item(item) for item in surface_view.items]
    answer_strategy = _answer_strategy_from_surface(surface_view.mode)
    query_result = QueryResult(
        summary_text=str(frame.metadata.get("summary_text") or surface_view.lead_text or ""),
        items=query_items,
        has_more=bool(frame.metadata.get("has_more")),
        query_contract=contract,
        surface_view=surface_view,
        answer_strategy=answer_strategy,
    )

    query_frames = _query_frames_from_context_frames(context_frames or [frame])
    if not query_frames:
        query_frames = [
            build_query_frame(query_contract=contract, result=query_result, turn_index=1).model_dump(mode="json")
        ]

    return {
        "session_active": True,
        "query_contract": contract.model_dump(mode="json"),
        "query_result": query_result.model_dump(mode="json"),
        "query_frames": query_frames,
        "current_page": int(frame.metadata.get("current_page") or 0),
        "page_size": int(frame.metadata.get("page_size") or 5),
        "show_expanded": False,
        "timestamp": frame.created_at_ts,
        "_query_session_source": "context_frame",
    }


def _query_frames_from_context_frames(frames: list[ContextFrame]) -> list[dict[str, Any]]:
    active_query_frames = [
        frame for frame in frames if isinstance(frame, ContextFrame) and query_surface_is_active(frame)
    ]
    query_frames: list[dict[str, Any]] = []
    for turn_index, frame in enumerate(active_query_frames[-3:], 1):
        raw_query_frame = frame.metadata.get("query_frame")
        if isinstance(raw_query_frame, dict):
            query_frames.append(raw_query_frame)
            continue

        contract = _restore_query_contract(frame.metadata.get("query_contract"))
        if contract is None:
            continue
        surface_view = _surface_view_from_frame(frame)
        query_result = QueryResult(
            summary_text=str(frame.metadata.get("summary_text") or surface_view.lead_text or ""),
            items=[_query_item_from_surface_item(item) for item in surface_view.items],
            has_more=bool(frame.metadata.get("has_more")),
            query_contract=contract,
            surface_view=surface_view,
            answer_strategy=_answer_strategy_from_surface(surface_view.mode),
        )
        query_frames.append(
            build_query_frame(query_contract=contract, result=query_result, turn_index=turn_index).model_dump(
                mode="json"
            )
        )
    return query_frames


def _restore_query_contract(raw: Any) -> QueryExecutionContract | None:
    if isinstance(raw, QueryExecutionContract):
        return raw
    if isinstance(raw, dict):
        try:
            return QueryExecutionContract.model_validate(raw)
        except Exception:
            return None
    return None


def _surface_view_from_frame(frame: ContextFrame) -> SurfaceView:
    raw_mode = frame.metadata.get("surface_mode")
    try:
        mode = SurfaceViewMode(str(raw_mode))
    except ValueError:
        mode = SurfaceViewMode.TRANSACTION_LIST

    items: list[SurfaceItemView] = []
    for idx, entity in enumerate(frame.items, 1):
        payload = entity.selection_payload
        if not isinstance(payload, SelectionPayload):
            payload = SelectionPayload(
                selection_kind="referent",
                entity_type=entity.entity_type.value,
                entity_id=entity.entity_id,
                label=entity.label or f"Result {idx}",
            )
        amount = _float_or_none(entity.data.get("amount"))
        count = _int_or_none(entity.data.get("count"))
        items.append(
            SurfaceItemView(
                id=str(entity.entity_id or payload.entity_id or idx),
                label=entity.label or payload.label,
                amount=amount,
                count=count,
                payload=payload,
                metadata=dict(entity.data),
            )
        )

    context = frame.metadata.get("surface_context")
    return SurfaceView(
        mode=mode,
        items=items,
        lead_text=str(frame.metadata.get("lead_text") or "") or None,
        context=context if isinstance(context, dict) else {},
    )


def _query_item_from_surface_item(item: SurfaceItemView) -> QueryResultItem:
    metadata = dict(item.metadata)
    return QueryResultItem(
        id=item.id,
        description=item.label,
        amount=float(item.amount or 0.0),
        date=_date_from_metadata(metadata),
        metadata=metadata,
    )


def _date_from_metadata(metadata: dict[str, Any]) -> date:
    for key in ("date", "transaction_date", "created_at"):
        raw = metadata.get(key)
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                continue
    return date.today()


def _answer_strategy_from_surface(mode: SurfaceViewMode) -> QueryAnswerStrategy:
    if mode == SurfaceViewMode.DIRECT_ANSWER:
        return QueryAnswerStrategy.DIRECT_ANSWER
    if mode == SurfaceViewMode.GROUPED_SUMMARY:
        return QueryAnswerStrategy.SUMMARY_LIST
    if mode == SurfaceViewMode.CLARIFICATION:
        return QueryAnswerStrategy.CLARIFY
    return QueryAnswerStrategy.TRANSACTION_LIST


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "build_query_context_for_worker",
    "build_query_session_snapshot_from_surface",
    "get_active_query_surface",
    "query_surface_is_active",
    "summarize_query_surface_for_planner",
]
