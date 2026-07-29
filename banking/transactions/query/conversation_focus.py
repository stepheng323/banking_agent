"""Deterministic salience resolution for query conversations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.conversation import FocusSubject, QueryFocus
from banking.transactions.query.models.domain import QueryFrame
from banking.transactions.query.models.operations import (
    AnalyzeOperation,
    CompareOperation,
    GroupedSummarySpec,
    QueryRequest,
    RetrieveOperation,
    SummarizeOperation,
)


def focus_for_request(
    request: QueryRequest,
    *,
    frame_id: str | None = None,
    step_id: str | None = None,
    source: str = "user_query",
    selected_payload: SelectionPayload | None = None,
    turn_id: str | None = None,
) -> QueryFocus:
    """Project authoritative operation semantics into stable conversation focus."""
    operation = request.operation
    if isinstance(operation, RetrieveOperation):
        subject, measure, statistic, dimension = "transactions", None, None, None
    elif isinstance(operation, SummarizeOperation):
        summary = operation.summary
        subject = "summary"
        measure = getattr(summary, "measure", None)
        statistic = getattr(summary, "statistic", None)
        dimension = summary.dimension if isinstance(summary, GroupedSummarySpec) else getattr(summary, "group_by", None)
    elif isinstance(operation, CompareOperation):
        subject, measure, statistic, dimension = "comparison", None, None, None
    elif isinstance(operation, AnalyzeOperation):
        analysis = operation.analysis
        subject = "insight"
        measure = getattr(analysis, "measure", None)
        statistic = None
        dimensions = getattr(analysis, "dimensions", None)
        dimension = dimensions[0] if isinstance(dimensions, list) and dimensions else None
    else:
        subject, measure, statistic, dimension = "affordability", None, None, None

    return QueryFocus(
        frame_id=frame_id,
        step_id=step_id,
        subject=cast("FocusSubject", subject),
        measure=measure,
        statistic=statistic,
        dimension=dimension,
        account_scope=getattr(request.accounts, "type", None),
        selected_payload=selected_payload,
        source=source,  # type: ignore[arg-type]
        originating_turn_id=turn_id,
        latest_user_turn_id=turn_id,
    )


def resolve_focus(
    *,
    frames: Iterable[QueryFrame],
    active_focus: QueryFocus | None,
    explicit_frame_id: str | None = None,
    explicit_payload: SelectionPayload | None = None,
) -> QueryFocus | None:
    """Resolve follow-up salience without treating display evidence as intent."""
    frame_list = list(frames)
    frame_by_id = {frame.frame_id: frame for frame in frame_list}
    if explicit_frame_id and explicit_frame_id in frame_by_id:
        frame = frame_by_id[explicit_frame_id]
        return (frame.focus or focus_for_request(frame.query_request, frame_id=frame.frame_id)).model_copy(
            update={
                "selected_payload": explicit_payload,
                "source": "user_selection" if explicit_payload else "user_refinement",
            }
        )
    if explicit_payload is not None:
        for frame in reversed(frame_list):
            if any(
                isinstance(item, dict)
                and isinstance(item.get("selection_payload"), dict)
                and item["selection_payload"].get("entity_id") == explicit_payload.entity_id
                for item in frame.visible_items
            ):
                return (frame.focus or focus_for_request(frame.query_request, frame_id=frame.frame_id)).model_copy(
                    update={"selected_payload": explicit_payload, "source": "user_selection"}
                )
    if active_focus is not None:
        return active_focus
    if frame_list:
        frame = frame_list[-1]
        return frame.focus or focus_for_request(frame.query_request, frame_id=frame.frame_id)
    return None


_DISPLAY_ONLY_CONTINUATIONS = {
    "coverage",
    "explain_aggregate_scope",
    "repeat_query",
    "show_evidence",
    "show_more",
}


def advance_focus(
    *,
    request: QueryRequest,
    previous: QueryFocus | None,
    continuation_type: str | None,
    selected_payload: SelectionPayload | None = None,
    source_frame_id: str | None = None,
    turn_id: str | None = None,
) -> QueryFocus:
    """Apply the focus lifecycle after one deterministic query transition.

    Pagination, replay and evidence change what is displayed, not what the
    user is discussing. Explicit selection takes focus; other contract edits
    become user refinements. A request without prior focus begins a new topic.
    """
    if selected_payload is not None:
        base = focus_for_request(
            request,
            frame_id=source_frame_id or (previous.frame_id if previous else None),
            source="user_selection",
            selected_payload=selected_payload,
            turn_id=turn_id,
        )
        if previous is not None and base.originating_turn_id is None:
            base.originating_turn_id = previous.originating_turn_id
        return base
    if previous is not None and continuation_type in _DISPLAY_ONLY_CONTINUATIONS:
        return previous.model_copy(update={"latest_user_turn_id": turn_id or previous.latest_user_turn_id})
    source = "user_query" if previous is None else "user_refinement"
    updated = focus_for_request(
        request,
        frame_id=source_frame_id or (previous.frame_id if previous else None),
        source=source,
        turn_id=turn_id,
    )
    if previous is not None:
        updated.originating_turn_id = previous.originating_turn_id
    return updated


__all__ = ["advance_focus", "focus_for_request", "resolve_focus"]
