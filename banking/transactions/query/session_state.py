"""Construction and safe projection of the v3 query session envelope."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import TypeAdapter

from banking.transactions.query.conversation_focus import focus_for_request, resolve_focus
from banking.transactions.query.grounding.frames import restore_query_frames
from banking.transactions.query.models.conversation import (
    PendingFieldClarification,
    PendingInterpretationProposal,
    QueryExecutionContract,
    QueryFocus,
    QueryInputCandidate,
    QuerySessionV3,
    QueryTurnPlan,
    SingleQueryExecution,
)
from banking.transactions.query.models.domain import QueryIntent, QueryResult
from banking.transactions.query.models.extraction import (
    Ambiguity,
    ClarificationCandidate,
    ClarificationOperation,
    PendingClarificationState,
    QueryExtractionResult,
)
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


def pending_input_from_legacy(raw: object) -> PendingFieldClarification | None:
    """Translate old clarification state once at the v3 persistence boundary."""
    try:
        pending = raw if isinstance(raw, PendingClarificationState) else PendingClarificationState.model_validate(raw)
    except Exception:
        return None
    request: QueryRequest | None = None
    if isinstance(pending.query_request, dict):
        try:
            request = QueryRequest.model_validate(pending.query_request)
        except Exception:
            request = None
    return PendingFieldClarification(
        source_frame_id=next(
            (candidate.frame_id for candidate in pending.candidate_payloads if candidate.frame_id),
            None,
        ),
        original_query=pending.original_query,
        target_field=pending.target_field,
        candidate_payloads=[
            QueryInputCandidate(label=candidate.label, payload=candidate.payload, frame_id=candidate.frame_id)
            for candidate in pending.candidate_payloads[:5]
        ],
        original_operation=(
            pending.original_operation.model_dump(mode="json") if pending.original_operation is not None else {}
        ),
        query_request=request,
        original_extraction=(
            pending.original_extraction.model_dump(mode="json") if pending.original_extraction is not None else None
        ),
        ambiguities=[ambiguity.model_dump(mode="json") for ambiguity in pending.ambiguities],
        resolver_message=pending.resolver_message,
        language=pending.language,
        attempt_count=pending.attempt_count,
        created_turn_id=pending.created_turn_id,
    )


def legacy_pending_from_input(raw: object) -> PendingClarificationState | None:
    """Adapt v3 field input for the existing deterministic clarification resolver.

    The adapter is deliberately ephemeral: checkpoints contain only
    ``PendingFieldClarification``.  It can be removed when the resolver itself
    consumes ``PendingQueryInput`` directly.
    """
    try:
        pending = raw if isinstance(raw, PendingFieldClarification) else PendingFieldClarification.model_validate(raw)
    except Exception:
        return None
    try:
        extraction = (
            QueryExtractionResult.model_validate(pending.original_extraction)
            if isinstance(pending.original_extraction, dict)
            else None
        )
        operation = (
            ClarificationOperation.model_validate(pending.original_operation)
            if pending.original_operation
            else None
        )
        return PendingClarificationState(
            original_query=pending.original_query,
            current_intent=extraction.intent if extraction is not None else QueryIntent.TRANSACTION_LIST,
            original_extraction=extraction,
            ambiguities=_restore_ambiguities(pending.ambiguities),
            resolver_message=pending.resolver_message,
            language=pending.language,
            clarification_type=cast_clarification_type(pending.target_field),
            target_field=pending.target_field,
            candidate_payloads=[
                ClarificationCandidate(label=candidate.label, payload=candidate.payload, frame_id=candidate.frame_id)
                for candidate in pending.candidate_payloads
            ],
            original_operation=operation,
            query_request=pending.query_request.model_dump(mode="json") if pending.query_request is not None else None,
            attempt_count=pending.attempt_count,
            created_turn_id=pending.created_turn_id,
        )
    except Exception:
        return None


ClarificationType = Literal[
    "time", "selection", "recipient", "account", "direction", "category", "status", "amount", "scope"
]


def cast_clarification_type(value: str | None) -> ClarificationType | None:
    allowed = {"time", "selection", "recipient", "account", "direction", "category", "status", "amount", "scope"}
    return cast(ClarificationType, value) if value in allowed else None


def _restore_ambiguities(raw: list[dict[str, object]]) -> list[Ambiguity]:
    restored: list[Ambiguity] = []
    for item in raw:
        try:
            restored.append(Ambiguity.model_validate(item))
        except Exception:
            continue
    return restored


def project_query_session_v3(raw: object) -> dict[str, Any] | None:
    """Create ephemeral worker state from a v3 checkpoint envelope."""
    try:
        session = raw if isinstance(raw, QuerySessionV3) else QuerySessionV3.model_validate(raw)
    except Exception:
        return None
    contract = session.execution_contract
    request: QueryRequest | None
    if isinstance(contract, SingleQueryExecution):
        request = contract.request
    elif isinstance(contract, QueryTurnPlan):
        primary = next((step for step in contract.steps if step.role == "primary"), None)
        request = primary.request if primary is not None else None
    else:
        request = None
    pending = session.pending_input
    legacy_pending = legacy_pending_from_input(pending) if isinstance(pending, PendingFieldClarification) else None
    return {
        "session_active": session.session_active,
        "query_request": request.model_dump(mode="json") if request is not None else None,
        "execution_contract": contract.model_dump(mode="json") if contract is not None else None,
        "query_result": session.display_result,
        "query_frames": session.query_frames,
        "pending_query_input": pending.model_dump(mode="json") if pending is not None else None,
        "pending_clarification": legacy_pending.model_dump(mode="json") if legacy_pending is not None else None,
        "current_page": session.current_page,
        "page_size": session.page_size,
        "show_expanded": session.show_expanded,
        "timestamp": session.timestamp,
        "recent_read_only": session.recent_read_only,
        "active_focus": session.active_focus.model_dump(mode="json") if session.active_focus is not None else None,
        "display_frame_id": session.display_frame_id,
        **session.cache,
    }


__all__ = [
    "build_query_session_v3",
    "legacy_pending_from_input",
    "pending_input_from_legacy",
    "project_query_session_v3",
]
