"""Structured, resumable clarification helpers for query conversations."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.conversation import SingleQueryExecution
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import (
    ClarificationCandidate,
    ClarificationOperation,
    PendingClarificationState,
    QueryExtractionResult,
)
from banking.transactions.query.models.operations import NamedCounterparty, QueryRequest
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_ORDINALS = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
}
_CANCEL = {"cancel", "neither", "none", "none of them", "neither one"}


def build_selection_clarification_updates(
    *,
    candidates: list[ClarificationCandidate],
    operation: ClarificationOperation,
    query_request: QueryRequest | None,
    locale: str,
    session: dict[str, Any],
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Persist candidates and the operation that should resume after selection."""
    bounded = candidates[:5]
    lines = "\n".join(f"{index}. {candidate.label}" for index, candidate in enumerate(bounded, 1))
    response = render_message("query.clarify.multiple_matches", locale, {"options": lines})
    intent = QueryIntent.TRANSACTION_LIST
    pending = PendingClarificationState(
        original_query="",
        current_intent=intent,
        original_extraction=QueryExtractionResult(intent=intent),
        resolver_message=response,
        language=locale,
        clarification_type="selection",
        target_field=operation.fact_field,
        candidate_payloads=bounded,
        original_operation=operation,
        query_request=query_request.model_dump(mode="json") if query_request is not None else None,
        created_turn_id=turn_id,
    )
    logger.info(
        "query_clarification_created",
        clarification_type="selection",
        candidate_count=len(bounded),
        candidate_sources=sorted({"frame" if candidate.frame_id else "active" for candidate in bounded}),
    )
    updates: dict[str, Any] = {
        "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
        "response": response,
        "session_active": True,
        "flow_state": "parsing",
        "pending_clarification": pending.model_dump(mode="json"),
        "show_expanded": bool(session.get("show_expanded", False)),
        "current_page": int(session.get("current_page", 0) or 0),
    }
    for key in ("query_result", "query_frames", "query_request", "page_size"):
        if session.get(key) is not None:
            updates[key] = session[key]
    return updates


def resolve_selection_clarification(
    pending: PendingClarificationState,
    message: str,
    *,
    locale: str,
    session: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve deterministic candidate replies; return None for semantic fallback."""
    if pending.clarification_type != "selection" or not pending.candidate_payloads:
        return None
    normalized = " ".join(message.casefold().split())
    if normalized in _CANCEL:
        logger.info("query_clarification_cancelled", clarification_type="selection")
        return {
            "transaction_outcome": TransactionOutcome.OK,
            "response": render_message("query.clarify.cancelled", locale),
            "session_active": False,
            "flow_state": "complete",
            "pending_clarification": None,
        }

    candidate = _match_candidate(pending.candidate_payloads, normalized)
    if candidate is not None:
        operation = pending.original_operation or ClarificationOperation(continuation_type="drill_down")
        logger.info(
            "query_clarification_resolved",
            clarification_type="selection",
            candidate_source="frame" if candidate.frame_id else "active",
            operation_restored=bool(pending.original_operation),
        )
        if operation.grounded_operation == "recipient_filter":
            request = _recipient_filter_request(pending, candidate.payload)
            if request is None:
                return {
                    "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                    "response": render_message("query.clarify.unsure_rephrase", locale),
                    "session_active": True,
                    "flow_state": "parsing",
                    "pending_clarification": None,
                }
            return {
                "query_request": request,
                # Recipient selection completes the original read operation.
                # Carry an explicit execution contract so the worker executes
                # this exact filtered request instead of treating the reply as
                # a generic active-result continuation on the next turn.
                "execution_contract": SingleQueryExecution(request=request),
                "execute_query_plan": True,
                "query_result": None,
                "flow_state": "executing",
                "session_active": True,
                "pending_clarification": None,
                "current_page": 0,
                "show_expanded": False,
                "continuation_type": "recipient_filter",
                "resolver_message": None,
            }
        return {
            "flow_state": "executing",
            "session_active": True,
            "pending_clarification": None,
            "continuation_type": operation.continuation_type or "drill_down",
            "selected_payload": candidate.payload,
            "selected_item_id": candidate.payload.entity_id,
            "selected_frame_id": candidate.frame_id,
            "drill_down_action": operation.drill_down_action or "view_details",
            "fact_field": operation.fact_field,
            "resolver_message": None,
        }

    next_attempt = min(pending.attempt_count + 1, 2)
    if next_attempt >= 2:
        logger.info("query_clarification_exhausted", clarification_type="selection", attempt_count=next_attempt)
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.exhausted", locale),
            "session_active": True,
            "flow_state": "parsing",
            "pending_clarification": None,
        }
    reprompt = pending.model_copy(update={"attempt_count": next_attempt})
    logger.info("query_clarification_reprompted", clarification_type="selection", attempt_count=next_attempt)
    return {
        "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
        "response": render_message("query.clarify.reply_number_or_rephrase", locale),
        "session_active": True,
        "flow_state": "parsing",
        "pending_clarification": reprompt.model_dump(mode="json"),
        "show_expanded": bool(session.get("show_expanded", False)),
        "current_page": int(session.get("current_page", 0) or 0),
    }


def _match_candidate(candidates: list[ClarificationCandidate], normalized: str) -> ClarificationCandidate | None:
    if normalized.isdigit():
        index = int(normalized) - 1
        return candidates[index] if 0 <= index < len(candidates) else None
    for token, index in _ORDINALS.items():
        if re.search(rf"\b{re.escape(token)}\b", normalized):
            return candidates[index] if index < len(candidates) else None
    exact = [candidate for candidate in candidates if candidate.label.casefold() == normalized]
    if len(exact) == 1:
        return exact[0]
    scored = [
        (SequenceMatcher(None, normalized, candidate.label.casefold()).ratio(), candidate)
        for candidate in candidates
        if normalized
    ]
    strong = [pair for pair in scored if pair[0] >= 0.78]
    if not strong:
        return None
    strong.sort(key=lambda pair: pair[0], reverse=True)
    if len(strong) > 1 and abs(strong[0][0] - strong[1][0]) < 0.05:
        return None
    return strong[0][1]


def _recipient_filter_request(
    pending: PendingClarificationState,
    payload: SelectionPayload,
) -> QueryRequest | None:
    if not isinstance(pending.query_request, dict):
        return None
    try:
        request = QueryRequest.model_validate(pending.query_request)
    except Exception:
        return None
    raw_counterparties = payload.filters_patch.get("counterparty") if isinstance(payload.filters_patch, dict) else None
    counterparties = [str(value).strip() for value in raw_counterparties or [] if str(value).strip()]
    if not counterparties:
        return None
    scope = request.scope
    if scope is None or scope.predicate.counterparty is None:
        return None
    reference = NamedCounterparty(name=counterparties[0])
    counterparty = scope.predicate.counterparty.model_copy(update={"reference": reference})
    predicate = scope.predicate.model_copy(update={"counterparty": counterparty})
    grounded_scope = scope.model_copy(update={"predicate": predicate})
    operation = request.operation.model_copy(update={"scope": grounded_scope})
    return request.model_copy(update={"operation": operation})


def clarification_candidate(
    *, payload: SelectionPayload, label: str, frame_id: str | None = None
) -> ClarificationCandidate:
    return ClarificationCandidate(label=label, payload=payload, frame_id=frame_id)


__all__ = [
    "build_selection_clarification_updates",
    "clarification_candidate",
    "resolve_selection_clarification",
]
