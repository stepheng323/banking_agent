"""Grounded memory follow-up handlers for active query sessions."""

from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.grounding.frames import build_grounded_query_request
from banking.transactions.query.grounding.memory import build_memory_answer


def resolve_grounded_followup(
    step: Any,
    *,
    decision: Any,
    session: dict[str, Any],
    language: str,
) -> dict[str, Any] | None:
    answer_mode = getattr(decision, "answer_mode", None)
    if answer_mode is None:
        return None

    query_frames = step._load_query_frames(session)
    frame_ids = getattr(decision, "referenced_frame_ids", None)
    operation = getattr(decision, "grounded_operation", None)

    if answer_mode == "ask_clarify":
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": render_message("query.clarify.missing_scope", language),
            "flow_state": "parsing",
            "session_active": True,
            "pending_clarification": None,
            "show_expanded": bool(session.get("show_expanded", False)),
            "current_page": session.get("current_page", 0),
        }

    if answer_mode == "memory_answer":
        response = build_memory_answer(
            query_frames=query_frames,
            frame_ids=frame_ids,
            operation=operation,
            language=language,
        )
        if response is None:
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": render_message("query.clarify.memory_unavailable", language),
                "flow_state": "parsing",
                "session_active": True,
                "pending_clarification": None,
                "show_expanded": bool(session.get("show_expanded", False)),
                "current_page": session.get("current_page", 0),
            }
        return {
            "response": response,
            "flow_state": "complete",
            "session_active": True,
            "pending_clarification": None,
            "resolver_message": None,
            "show_expanded": bool(session.get("show_expanded", False)),
            "current_page": session.get("current_page", 0),
        }

    if answer_mode == "grounded_query":
        query_request = build_grounded_query_request(
            query_frames=query_frames,
            frame_ids=frame_ids,
            operation=operation,
        )
        if query_request is None:
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": render_message("query.clarify.grounded_query_failed", language),
                "flow_state": "parsing",
                "session_active": True,
                "pending_clarification": None,
                "show_expanded": bool(session.get("show_expanded", False)),
                "current_page": session.get("current_page", 0),
            }
        return {
            "flow_state": "executing",
            "resolver_message": None,
            "query_request": query_request,
            "current_page": 0,
            "show_expanded": False,
        }

    return None


__all__ = ["resolve_grounded_followup"]
