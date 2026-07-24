"""Pending clarification handling for query extraction."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import banking.transactions.query.continuations.compiler_paths as compiler_paths
from banking.presentation.i18n.locale import LocaleManager
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.continuations.clarification_state import resolve_selection_clarification
from banking.transactions.query.continuations.grounded_followups import resolve_grounded_followup
from banking.transactions.query.models.extraction import AmbiguityCode, ClarificationPatch, QueryExtractionResult
from banking.transactions.query.utils.timezone import lagos_today
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

    selection_updates = resolve_selection_clarification(pending, message, locale=locale, session=session)
    if selection_updates is not None:
        return selection_updates

    deterministic_result = step.parser.parse_deterministic(message, today=today, language=locale)
    if deterministic_result is not None:
        updates = compiler_paths.parse_result_to_updates(
            step, deterministic_result, state=state, today=today, language=locale
        )
        return step._append_query_session_transition(updates, "replace_session_new_query")

    clarification_time_range = step.parser.parse_clarification_time_range(message, today=today)
    if clarification_time_range is None and looks_like_explicit_fresh_query_interrupt(message):
        updates = await compiler_paths.parse_new_query(step, state)
        return step._append_query_session_transition(updates, "replace_session_new_query")

    deterministic_patch = _deterministic_clarification_patch(
        pending.clarification_type,
        message,
        parsed_time_range=clarification_time_range,
    )
    if deterministic_patch is not None and pending.original_extraction is not None:
        patched = _apply_clarification_patch(pending.original_extraction, deterministic_patch)
        result = step.parser.compile_extraction(patched, today=today, language=locale)
        logger.info(
            "query_clarification_resolved",
            clarification_type=pending.clarification_type,
            resolution="deterministic_patch",
        )
        return compiler_paths.parse_result_to_updates(step, result, state=state, today=today, language=locale)

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

    if decision.reason == "query_reasoner_timeout" and decision.response_text:
        return {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": decision.response_text,
            "session_active": True,
            "pending_clarification": pending,
            "flow_state": "parsing",
            **step._semantic_trace_updates(decision),
        }

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
        if pending.original_extraction is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        patched_extraction = pending.original_extraction.model_copy(deep=True)
        if decision.clarification_patch is not None:
            patched_extraction = _apply_clarification_patch(patched_extraction, decision.clarification_patch)
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


def _apply_clarification_patch(extraction: QueryExtractionResult, patch: ClarificationPatch) -> QueryExtractionResult:
    """Apply only explicit typed fields and retain unrelated ambiguities."""
    updated = extraction.model_copy(deep=True)
    resolved_codes: set[AmbiguityCode] = set()
    if patch.time_range is not None:
        updated.time_range = patch.time_range.model_copy(deep=True)
        resolved_codes.add(AmbiguityCode.TIME_VAGUE)
    if patch.recipient is not None:
        updated.filters.recipient = patch.recipient
        resolved_codes.add(AmbiguityCode.RECIPIENT_VAGUE)
    if patch.account_filter is not None:
        updated.filters.bank = patch.account_filter
    if patch.transaction_type is not None:
        updated.filters.transaction_type = patch.transaction_type
    if patch.category is not None:
        updated.filters.category = patch.category
    if patch.status is not None:
        updated.filters.status = patch.status
    if patch.min_amount is not None:
        updated.filters.min_amount = patch.min_amount
        resolved_codes.add(AmbiguityCode.AMOUNT_VAGUE)
    if patch.max_amount is not None:
        updated.filters.max_amount = patch.max_amount
        resolved_codes.add(AmbiguityCode.AMOUNT_VAGUE)
    updated.ambiguities = [ambiguity for ambiguity in updated.ambiguities if ambiguity.code not in resolved_codes]
    return updated


def _deterministic_clarification_patch(
    clarification_type: str | None,
    message: str,
    *,
    parsed_time_range: Any | None,
) -> ClarificationPatch | None:
    normalized = " ".join((message or "").casefold().split())
    if clarification_type == "time" and parsed_time_range is not None:
        return ClarificationPatch(time_range=parsed_time_range)
    if clarification_type == "direction":
        if normalized in {"credit", "credits", "income", "money in", "incoming"}:
            return ClarificationPatch(transaction_type="credit")
        if normalized in {"debit", "debits", "spending", "money out", "outgoing"}:
            return ClarificationPatch(transaction_type="debit")
    if clarification_type == "status":
        status_aliases = {
            "success": "successful",
            "successful": "successful",
            "failed": "failed",
            "pending": "pending",
            "reversed": "reversed",
        }
        status = status_aliases.get(normalized)
        if status is not None:
            return ClarificationPatch(status=status)  # type: ignore[arg-type]
    if clarification_type == "amount":
        values = [float(raw.replace(",", "")) for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", normalized)]
        if len(values) >= 2:
            return ClarificationPatch(min_amount=min(values), max_amount=max(values))
        if len(values) == 1:
            return ClarificationPatch(min_amount=values[0])
    if clarification_type in {"recipient", "account", "category"} and normalized:
        if clarification_type == "recipient":
            return ClarificationPatch(recipient=message.strip())
        if clarification_type == "account":
            return ClarificationPatch(account_filter=message.strip())
        return ClarificationPatch(category=message.strip())
    return None
