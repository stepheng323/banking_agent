"""Pending clarification handling for query extraction."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import apps.chat.src.agent.workers.query.continuations.compiler_paths as compiler_paths
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.query.continuations.grounded_followups import resolve_grounded_followup
from apps.chat.src.agent.workers.query.models.extraction import AmbiguityCode
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from banking.presentation.i18n.locale import LocaleManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_FRESH_QUERY_INTERRUPT_HEAD_RE = re.compile(
    r"^(?:show|list|view|get|check|display|see)\b.*\b(?:transactions?|transaction|debits?|credits?|payments?)\b",
    re.IGNORECASE,
)


def looks_like_explicit_fresh_query_interrupt(message: str) -> bool:
    normalized = " ".join((message or "").strip().lower().split())
    if not normalized:
        return False
    return bool(_FRESH_QUERY_INTERRUPT_HEAD_RE.match(normalized))


async def handle_pending_clarification(step: Any, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Resolve a follow-up against an unresolved semantic query state."""
    pending = step._load_pending_clarification(session)
    if pending is None:
        return await compiler_paths.parse_new_query(step, state)

    message = state.get("message", "")
    today_state = state.get("today")
    today = today_state if isinstance(today_state, date) else lagos_today()
    locale = LocaleManager.normalize(state.get("language")).value

    deterministic_result = step.parser.parse_deterministic(message, today=today, language=locale)
    if deterministic_result is not None:
        updates = compiler_paths.parse_result_to_updates(step, deterministic_result, state=state, today=today, language=locale)
        return step._append_query_session_transition(updates, "replace_session_new_query")

    clarification_time_range = step.parser.parse_clarification_time_range(message, today=today)
    if clarification_time_range is None and looks_like_explicit_fresh_query_interrupt(message):
        updates = await compiler_paths.parse_new_query(step, state)
        return step._append_query_session_transition(updates, "replace_session_new_query")

    decision = await step.reasoner.reason(
        step._build_reasoner_context(
            message=message,
            today=today,
            language=locale,
            state=state,
            pending_clarification=pending,
            query_frames=step._load_query_frames(session),
        )
    )
    logger.info(
        "query_pending_clarification_resolved",
        decision=decision.decision,
        reason=decision.reason,
        confidence=decision.confidence,
    )

    if decision.decision == "end_session":
        return step._append_query_session_transition(
            {
                "transaction_outcome": TransactionOutcome.OK,
                "response": step._resolve_end_session_response(decision, locale=locale),
                "session_active": False,
                "pending_clarification": None,
                "flow_state": "complete",
                **step._semantic_trace_updates(decision),
            },
            "end_query_session",
        )

    if decision.decision == "new_query":
        return await compiler_paths.parse_reasoner_extraction_to_updates(
            step,
            decision,
            state=state,
            today=today,
            language=locale,
        )

    grounded_updates = resolve_grounded_followup(
        step,
        decision=decision,
        session=session,
        language=locale,
    )
    if grounded_updates is not None:
        grounded_updates.update(step._semantic_trace_updates(decision))
        return grounded_updates

    if decision.decision == "clarification_answer":
        patched_extraction = pending.original_extraction.model_copy(deep=True)
        if decision.time_period:
            parsed_time_range = step.parser.parse_clarification_time_range(decision.time_period, today=today)
            if parsed_time_range is not None:
                patched_extraction.time_range = parsed_time_range
                patched_extraction.ambiguities = [
                    ambiguity
                    for ambiguity in patched_extraction.ambiguities
                    if ambiguity.code != AmbiguityCode.TIME_VAGUE
                ]
        result = step.parser.compile_extraction(
            patched_extraction,
            today=today,
            language=locale,
        )
        updates = compiler_paths.parse_result_to_updates(step, result, state=state, today=today, language=locale)
        updates.update(step._semantic_trace_updates(decision))
        return updates

    updates = await compiler_paths.parse_reasoner_extraction_to_updates(
        step,
        decision,
        state=state,
        today=today,
        language=locale,
    )
    updates.update(step._semantic_trace_updates(decision))
    return updates
