"""Construction and typed access for the v3 query session envelope."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from banking.transactions.query.conversation_focus import focus_for_request, resolve_focus
from banking.transactions.query.grounding.frames import restore_query_frames
from banking.transactions.query.models.conversation import (
    PendingFieldClarification,
    PendingInterpretationProposal,
    QueryExecutionContract,
    QueryFocus,
    QuerySessionV3,
    QueryTurnPlan,
    SingleQueryExecution,
)
from banking.transactions.query.models.domain import QueryResult
from banking.transactions.query.models.operations import QueryRequest

_CACHE_KEYS = (
    "cached_transactions",
    "cache_fetched_at",
    "cache_fingerprint",
    "cache_scope_fingerprint",
    "cache_window_start",
    "cache_window_end",
)


def _safe_result_snapshot(result: QueryResult | dict[str, Any] | None) -> dict[str, object] | None:
    if result is None:
        return None
    try:
        parsed = result if isinstance(result, QueryResult) else QueryResult.model_validate(result)
    except Exception:
        return None
    payload = parsed.model_dump(mode="json")
    # Fetch-cache rows are a performance implementation detail, never
    # conversational memory. Visible surface items are retained separately.
    for key in _CACHE_KEYS:
        payload.pop(key, None)
    return payload


def build_query_session_v3(
    *,
    request: QueryRequest | None,
    result: QueryResult | dict[str, Any] | None,
    raw_frames: object,
    current_page: int = 0,
    page_size: int = 5,
    show_expanded: bool = False,
    timestamp: float | None = None,
    active_focus: QueryFocus | None = None,
    execution_contract: object | None = None,
    display_frame_id: str | None = None,
    pending_input: PendingFieldClarification | PendingInterpretationProposal | None = None,
    recent_read_only: bool = False,
    cache: dict[str, object] | None = None,
) -> QuerySessionV3:
    frames = restore_query_frames(raw_frames)
    parsed_result: QueryResult | None = None
    if isinstance(result, QueryResult):
        parsed_result = result
    elif isinstance(result, dict):
        try:
            parsed_result = QueryResult.model_validate(result)
        except Exception:
            parsed_result = None
    focus = active_focus or (parsed_result.conversation_focus if parsed_result is not None else None)
    if focus is None:
        focus = resolve_focus(frames=frames, active_focus=None)
    if focus is None and request is not None:
        focus = focus_for_request(request)
    resolved_execution: QueryExecutionContract | None = None
    if execution_contract is not None:
        try:
            resolved_execution = TypeAdapter(QueryExecutionContract).validate_python(execution_contract)
        except Exception:
            resolved_execution = None
    if resolved_execution is None and parsed_result is not None:
        resolved_execution = parsed_result.execution_contract
    if resolved_execution is None and frames:
        resolved_execution = frames[-1].execution_contract
    if resolved_execution is None and request is not None:
        resolved_execution = SingleQueryExecution(request=request)
    return QuerySessionV3(
        execution_contract=resolved_execution,
        active_focus=focus,
        display_frame_id=display_frame_id or (frames[-1].frame_id if frames else None),
        display_result=_safe_result_snapshot(result),
        query_frames=[frame.model_dump(mode="json") for frame in frames],
        pending_input=pending_input,
        current_page=max(0, current_page),
        page_size=page_size,
        show_expanded=show_expanded,
        timestamp=timestamp,
        recent_read_only=recent_read_only,
        cache=cache or {},
    )


def restore_query_session_v3(raw: object) -> QuerySessionV3 | None:
    """Validate the sole query-session checkpoint contract."""
    try:
        return raw if isinstance(raw, QuerySessionV3) else QuerySessionV3.model_validate(raw)
    except Exception:
        return None


def session_query_request(session: QuerySessionV3) -> QueryRequest | None:
    """Return the primary executable request without creating flat state authority."""
    contract = session.execution_contract
    if isinstance(contract, SingleQueryExecution):
        return contract.request
    if isinstance(contract, QueryTurnPlan):
        primary = next((step for step in contract.steps if step.role == "primary"), None)
        return primary.request if primary is not None else None
    return None


__all__ = [
    "build_query_session_v3",
    "restore_query_session_v3",
    "session_query_request",
]
