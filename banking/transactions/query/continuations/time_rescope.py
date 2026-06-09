"""Time-rescope recovery for active query continuations."""

from datetime import date
from time import perf_counter
from typing import Any

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.compiler.time_ranges import build_time_range
from banking.transactions.query.continuations.transforms import rebuild_query_contract
from banking.transactions.query.models.domain import (
    QueryExecutionContract,
    QueryOperation,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    ExtractionIntent,
    QueryExtractionResult,
    QueryTimeRange,
    ResolverOutcome,
)
from banking.transactions.query.services.parsing.parser import QueryParser

_DIRECT_TIME_PREFIXES = (
    "what about ",
    "how about ",
    "for ",
    "only ",
    "just ",
    "and ",
)
_CORRECTION_TIME_PREFIXES = (
    "i said ",
    "i mean ",
    "i meant ",
    "i asked for ",
    "no ",
    "not ",
    "instead ",
    "rather ",
    "make it ",
    "change it to ",
    "use ",
)
_ASSERTIVE_CORRECTION_TIME_PREFIXES = (
    "i said ",
    "i mean ",
    "i meant ",
    "i asked for ",
)


def _normalize_time_rescope_message(message: str) -> str:
    return " ".join(message.strip().split()).lower().rstrip("?.!,")


def _has_parseable_time_candidate(
    normalized_message: str,
    *,
    today: date,
    prefixes: tuple[str, ...],
) -> bool:
    for prefix in prefixes:
        if not normalized_message.startswith(prefix):
            continue
        candidate = normalized_message[len(prefix) :].strip()
        if QueryParser.parse_clarification_time_range(candidate, today=today) is not None:
            return True
    return False


def _compile_clarification_time_range(parsed: QueryTimeRange, *, today: date) -> TimeRange | None:
    return build_time_range(
        QueryExtractionResult(time_range=parsed),
        today=today,
        effective_intent=ExtractionIntent.TRANSACTION_LIST,
        query_operation=QueryOperation.LIST_TRANSACTIONS,
        answer_fact_field=None,
        result_reference=None,
    )


def direct_time_rescope_range(message: str, *, today: date) -> TimeRange | None:
    """Return the time range for a direct active-query time-only follow-up."""
    normalized = _normalize_time_rescope_message(message)
    if not normalized:
        return None

    parsed = QueryParser.parse_clarification_time_range(normalized, today=today)
    if parsed is not None:
        return _compile_clarification_time_range(parsed, today=today)

    for prefix in _DIRECT_TIME_PREFIXES + _CORRECTION_TIME_PREFIXES:
        if not normalized.startswith(prefix):
            continue
        candidate = normalized[len(prefix) :].strip()
        parsed = QueryParser.parse_clarification_time_range(candidate, today=today)
        if parsed is not None:
            return _compile_clarification_time_range(parsed, today=today)
    return None


def is_single_day_direct_time_rescope_message(message: str, *, today: date) -> bool:
    """Return true for direct time-only follow-ups that resolve to one calendar day."""
    parsed = direct_time_rescope_range(message, today=today)
    return bool(parsed is not None and parsed.start == parsed.end)


def is_direct_time_rescope_message(message: str, *, today: date) -> bool:
    """Return true for active-query follow-ups that only change the time scope."""
    return direct_time_rescope_range(message, today=today) is not None


def is_correction_time_rescope_message(message: str, *, today: date) -> bool:
    """Return true for corrections like "I said yesterday" against an active query."""
    normalized = _normalize_time_rescope_message(message)
    if not normalized:
        return False
    return _has_parseable_time_candidate(
        normalized,
        today=today,
        prefixes=_ASSERTIVE_CORRECTION_TIME_PREFIXES,
    )


async def maybe_recover_time_rescope_continuation(
    step: Any,
    *,
    trigger_reason: str,
    decision: Any,
    state: dict[str, Any],
    session: dict[str, Any],
    session_query_contract: QueryExecutionContract | None,
    message: str,
    today: date,
    language: str,
) -> dict[str, Any] | None:
    del session
    if session_query_contract is None:
        step._log_time_rescope_recovery(
            trigger_reason=trigger_reason,
            recovered=False,
            session_has_query_contract=False,
            skip_reason="missing_query_contract",
        )
        return None

    if (
        trigger_reason in {"low_confidence_unclear", "unclear_continuation"}
        and getattr(decision, "continuation_type", None) != "time_delta"
        and getattr(decision, "time_range", None) is None
        and not getattr(decision, "time_period", None)
        and getattr(decision, "extraction", None) is None
        and not is_direct_time_rescope_message(message, today=today)
    ):
        step._log_time_rescope_recovery(
            trigger_reason=trigger_reason,
            recovered=False,
            session_has_query_contract=True,
            skip_reason="message_not_time_only",
        )
        return None

    resolved_time_range, clarification_message = await resolve_time_delta_range(
        step,
        decision=decision,
        message=message,
        today=today,
        language=language,
        state=state,
    )
    if resolved_time_range is None:
        step._log_time_rescope_recovery(
            trigger_reason=trigger_reason,
            recovered=False,
            session_has_query_contract=True,
            skip_reason="parser_requested_clarification" if clarification_message else "time_not_resolved",
        )
        return None

    step._log_time_rescope_recovery(
        trigger_reason=trigger_reason,
        recovered=True,
        session_has_query_contract=True,
        resolved_time_range=resolved_time_range,
        preserved_query_shape=True,
    )

    return {
        "flow_state": "executing",
        "continuation_type": "time_delta",
        "continuation_delta_type": "time",
        "resolver_message": None,
        "query_contract": rebuild_query_contract(
            session_query_contract,
            time_range=resolved_time_range,
            result_limit=decision.result_limit
            if decision.result_limit is not None
            else session_query_contract.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None
            else session_query_contract.result_reference,
            continuation_type="time_delta",
            continuation_delta_type="time",
        ),
        "current_page": 0,
        "show_expanded": False,
    }


async def resolve_time_delta_range(
    step: Any,
    *,
    decision: Any,
    message: str,
    today: date,
    language: str,
    state: dict[str, Any] | None = None,
) -> tuple[TimeRange | None, str | None]:
    started_at = perf_counter()
    semantic_decision = getattr(decision, "decision", None)
    continuation_type = getattr(decision, "continuation_type", None)
    direct_rescope = direct_time_rescope_range(message, today=today)
    if direct_rescope is not None:
        if state is not None:
            step._log_query_trace(
                state=state,
                phase="time_resolution",
                latency_ms=(perf_counter() - started_at) * 1000.0,
                outcome="resolved",
                resolution_source="direct_message_time_rescope",
                semantic_decision=semantic_decision,
                continuation_type=continuation_type,
            )
        return direct_rescope, None

    if decision.time_range is not None:
        resolved_time_range = decision.time_range
        if state is not None:
            step._log_query_trace(
                state=state,
                phase="time_resolution",
                latency_ms=(perf_counter() - started_at) * 1000.0,
                outcome="resolved",
                resolution_source="decision_time_range",
                semantic_decision=semantic_decision,
                continuation_type=continuation_type,
            )
        return resolved_time_range, None

    normalized_message = " ".join(message.strip().split())
    if normalized_message:
        parts = normalized_message.split()
        for start in range(len(parts)):
            candidate = " ".join(parts[start:])
            parsed_time_range = step.parser.parse_clarification_time_range(candidate, today=today)
            if parsed_time_range is None:
                continue
            query_ir = step.parser.build_query_ir_from_extraction(
                QueryExtractionResult(
                    time_range=parsed_time_range,
                    raw_query=message,
                ),
                today=today,
                language=language,
            )
            if state is not None:
                step._log_query_trace(
                    state=state,
                    phase="time_resolution",
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    outcome="resolved",
                    resolution_source="message_suffix_parse",
                    semantic_decision=semantic_decision,
                    continuation_type=continuation_type,
                )
            return query_ir.time_range, None

    extraction = getattr(decision, "extraction", None)
    if extraction is not None:
        if not extraction.raw_query:
            extraction = extraction.model_copy(update={"raw_query": message})
        result = step.parser.compile_extraction(extraction, today=today, language=language)
        if result.outcome == ResolverOutcome.NEEDS_INPUT:
            return None, result.resolver_message or render_message("query.clarify.default", language)

        query_contract = None
        if isinstance(result.query_contract, dict):
            try:
                query_contract = QueryExecutionContract.model_validate(result.query_contract)
            except Exception:
                query_contract = None

        if query_contract and query_contract.time_range is not None:
            if state is not None:
                step._log_query_trace(
                    state=state,
                    phase="time_resolution",
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    outcome="resolved",
                    resolution_source="decision_extraction_contract",
                    semantic_decision=semantic_decision,
                    continuation_type=continuation_type,
                )
            return query_contract.time_range, None

        if result.extraction is not None:
            query_ir = step.parser.build_query_ir_from_extraction(result.extraction, today=today, language=language)
            if state is not None:
                step._log_query_trace(
                    state=state,
                    phase="time_resolution",
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    outcome="resolved",
                    resolution_source="decision_extraction_ir",
                    semantic_decision=semantic_decision,
                    continuation_type=continuation_type,
                )
            return query_ir.time_range, None

    if decision.time_period:
        parsed_time_range = step.parser.parse_clarification_time_range(decision.time_period, today=today)
        if parsed_time_range is None:
            if state is not None:
                step._log_query_trace(
                    state=state,
                    phase="time_resolution",
                    latency_ms=(perf_counter() - started_at) * 1000.0,
                    outcome="unresolved",
                    resolution_source="decision_time_period_invalid",
                    semantic_decision=semantic_decision,
                    continuation_type=continuation_type,
                )
            return None, None
        query_ir = step.parser.build_query_ir_from_extraction(
            QueryExtractionResult(
                time_range=parsed_time_range,
                raw_query=message,
            ),
            today=today,
            language=language,
        )
        if state is not None:
            step._log_query_trace(
                state=state,
                phase="time_resolution",
                latency_ms=(perf_counter() - started_at) * 1000.0,
                outcome="resolved",
                resolution_source="decision_time_period",
                semantic_decision=semantic_decision,
                continuation_type=continuation_type,
            )
        return query_ir.time_range, None

    if state is not None:
        step._log_query_trace(
            state=state,
            phase="time_resolution",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            outcome="unresolved",
            resolution_source="none",
            semantic_decision=semantic_decision,
            continuation_type=continuation_type,
        )
    return None, None


__all__ = [
    "direct_time_rescope_range",
    "is_correction_time_rescope_message",
    "is_direct_time_rescope_message",
    "is_single_day_direct_time_rescope_message",
    "maybe_recover_time_rescope_continuation",
    "resolve_time_delta_range",
]
