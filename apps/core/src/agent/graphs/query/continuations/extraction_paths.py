"""Continuation and compiler path handlers for the query extraction step."""

from __future__ import annotations

import re
from datetime import date
from time import perf_counter
from typing import Any, Literal, cast

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    AmbiguityCode,
    PendingClarificationState,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    ResolverOutcome,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.presentation.selection_resolver import find_selection_payload
from apps.core.src.agent.graphs.query.presentation.surface_builder import apply_selection_payload_to_query
from apps.core.src.agent.graphs.query.services.grounding import (
    build_grounded_query_contract,
    build_memory_answer,
)
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_FRESH_QUERY_INTERRUPT_HEAD_RE = re.compile(
    r"^(?:show|list|view|get|check|display|see)\b.*\b(?:transactions?|transaction|debits?|credits?|payments?)\b",
    re.IGNORECASE,
)


def _looks_like_explicit_fresh_query_interrupt(message: str) -> bool:
    normalized = " ".join((message or "").strip().lower().split())
    if not normalized:
        return False
    return bool(_FRESH_QUERY_INTERRUPT_HEAD_RE.match(normalized))


def _has_specific_scope_filters(filters: Any | None) -> bool:
    if filters is None:
        return False
    return any(
        (
            bool(getattr(filters, "merchant", None)),
            bool(getattr(filters, "counterparty", None)),
            bool(getattr(filters, "category", None)),
            getattr(filters, "min_amount", None) is not None,
            getattr(filters, "max_amount", None) is not None,
            bool(getattr(filters, "exclude", None)),
            bool(getattr(filters, "account_filter", None)),
        )
    )


def _should_reset_inherited_aggregate_filters(
    *,
    extraction: QueryExtractionResult | None,
    session_query_contract: QueryExecutionContract | None,
    extracted_contract: QueryExecutionContract | None,
) -> bool:
    if extraction is None or session_query_contract is None or extracted_contract is None:
        return False
    if extraction.time_range.reference_type not in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}:
        return False
    if not _has_specific_scope_filters(session_query_contract.filters):
        return False
    return not _has_specific_scope_filters(extracted_contract.filters)


def _should_replace_aggregate_time_range(
    *,
    extraction: QueryExtractionResult | None,
    extracted_contract: QueryExecutionContract | None,
) -> bool:
    if extraction is None or extracted_contract is None:
        return False
    return extraction.time_range.reference_type in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}


async def maybe_recover_supported_followup_query(
    step: Any,
    *,
    state: dict[str, Any],
    today: date,
    language: str,
    has_original_scope: bool = False,
    reasoner_extraction: QueryExtractionResult | None = None,
    reasoner_confidence: float | None = None,
) -> dict[str, Any] | None:
    compiler_safe_extraction, compiler_safe_reason = step._compiler_safe_extraction_decision(
        extraction=reasoner_extraction,
        confidence=reasoner_confidence,
        has_original_scope=has_original_scope,
    )
    if compiler_safe_extraction is None:
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason=compiler_safe_reason,
            resolution_source="reasoner_extraction_compile",
        )
        return None

    if not compiler_safe_extraction.raw_query:
        compiler_safe_extraction = compiler_safe_extraction.model_copy(update={"raw_query": state.get("message", "")})
    parsed_result = step.parser.compile_extraction(
        compiler_safe_extraction,
        today=today,
        language=language,
    )
    resolution_source = "reasoner_extraction_compile"
    if parsed_result.outcome != ResolverOutcome.OK or parsed_result.extraction is None:
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason="parser_not_ok" if resolution_source == "parser_parse" else "compiler_not_ok",
            parser_outcome=parsed_result.outcome.value
            if hasattr(parsed_result.outcome, "value")
            else str(parsed_result.outcome),
            resolution_source=resolution_source,
        )
        return None

    query_contract: QueryExecutionContract | dict[str, Any] | None = parsed_result.query_contract
    if isinstance(query_contract, dict):
        try:
            query_contract = QueryExecutionContract.model_validate(query_contract)
        except Exception:
            query_contract = None
    if not isinstance(query_contract, QueryExecutionContract):
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason="missing_query_contract",
            resolution_source=resolution_source,
        )
        return None

    if not step._has_supported_followup_query_signal(query_contract):
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason="time_only_or_weak_query_signal",
            parsed_intent=query_contract.intent.value,
            resolution_source=resolution_source,
        )
        return None

    extraction = parsed_result.extraction
    if has_original_scope and extraction.time_range.reference_type == TimeReference.UNSPECIFIED:
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason="followup_reparse_requires_explicit_scope",
            parsed_intent=query_contract.intent.value,
            resolution_source=resolution_source,
        )
        return None

    logger.info(
        "query_continuation_resolution",
        path="fallback_parse_supported_query",
        recovered=True,
        parsed_intent=query_contract.intent.value,
        resolution_source=resolution_source,
    )
    recovered_updates = parse_result_to_updates(step, parsed_result, state=state, today=today, language=language)
    return step._append_query_session_transition(recovered_updates, "replace_session_new_query")


async def compile_aggregate_continuation_updates(
    step: Any,
    *,
    decision: Any,
    state: dict[str, Any],
    today: date,
    language: str,
    session_query_contract: QueryExecutionContract | None,
) -> dict[str, Any] | None:
    extraction = getattr(decision, "extraction", None)
    if extraction is not None and not extraction.raw_query:
        extraction = extraction.model_copy(update={"raw_query": state.get("message", "")})

    if session_query_contract is None:
        if extraction is None:
            return None
        patched_decision = (
            decision.model_copy(update={"extraction": extraction}) if hasattr(decision, "model_copy") else decision
        )
        return await parse_reasoner_extraction_to_updates(
            step,
            patched_decision,
            state=state,
            today=today,
            language=language,
        )

    extracted_contract: QueryExecutionContract | None = None
    if extraction is not None:
        compiled = step.parser.compile_extraction(extraction, today=today, language=language)
        extracted_contract = step._validated_query_contract(compiled.query_contract)
        if extracted_contract is not None and extracted_contract.intent in {
            QueryIntent.TIME_COMPARISON,
            QueryIntent.BENEFICIARY_SUMMARY,
            QueryIntent.AFFORDABILITY,
        }:
            patched_decision = decision.model_copy(update={"extraction": extraction}) if hasattr(decision, "model_copy") else decision
            return await parse_reasoner_extraction_to_updates(
                step,
                patched_decision,
                state=state,
                today=today,
                language=language,
            )

    updated_filters = extracted_contract.filters if extracted_contract is not None else None
    reset_inherited_filters = _should_reset_inherited_aggregate_filters(
        extraction=extraction,
        session_query_contract=session_query_contract,
        extracted_contract=extracted_contract,
    )
    replace_time_range = _should_replace_aggregate_time_range(
        extraction=extraction,
        extracted_contract=extracted_contract,
    )
    if reset_inherited_filters:
        logger.info(
            "query_aggregate_filter_reset",
            reason="explicit_aggregate_scope_without_specific_filter",
            previous_intent=session_query_contract.intent.value,
            previous_has_specific_filters=_has_specific_scope_filters(session_query_contract.filters),
            extracted_intent=extracted_contract.intent.value if extracted_contract is not None else None,
            extracted_time_reference=(
                extraction.time_range.reference_type.value if extraction is not None else None
            ),
        )
    if extracted_contract is not None and extracted_contract.aggregation is not None:
        aggregation = extracted_contract.aggregation.model_copy(deep=True)
    elif step._is_income_vs_spending_followup(message=state.get("message", ""), query_contract=session_query_contract):
        aggregation = Aggregation(type="breakdown", group_by="transaction_type")
        logger.info(
            "query_continuation_resolution",
            path="aggregate_income_vs_spending_fallback",
            recovered=True,
            original_intent=session_query_contract.intent.value,
        )
    else:
        aggregation = Aggregation(type="sum")

    from apps.core.src.agent.graphs.query.continuations.transforms import rebuild_query_contract

    query_contract = rebuild_query_contract(
        session_query_contract,
        filters=(
            updated_filters
            if reset_inherited_filters or updated_filters is not None
            else session_query_contract.filters
        ),
        merge_filters=updated_filters is not None and not reset_inherited_filters,
        time_range=(
            extracted_contract.to_query_ir().time_range
            if replace_time_range and extracted_contract is not None
            else session_query_contract.to_query_ir().time_range
        ),
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=aggregation,
        result_limit=extracted_contract.result_limit if extracted_contract is not None else None,
        result_reference=extracted_contract.result_reference if extracted_contract is not None else None,
        continuation_type=getattr(decision, "continuation_type", None),
        continuation_delta_type=getattr(decision, "delta_type", None),
    )
    logger.info(
        "query_aggregate_continuation_compiled",
        source="reasoner_extraction" if extracted_contract is not None else "active_query_scope_default",
        aggregation_type=query_contract.aggregation.type if query_contract.aggregation is not None else None,
        time_start=query_contract.time_start.isoformat(),
        time_end=query_contract.time_end.isoformat(),
        has_filters=bool(query_contract.filters),
    )
    return {
        "query_contract": query_contract,
        "resolver_message": None,
        "flow_state": "executing",
        "current_page": 0,
        "session_active": True,
        "pending_clarification": None,
        "show_expanded": False,
    }


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
        and not step._is_direct_time_rescope_message(message, today=today)
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
    from apps.core.src.agent.graphs.query.continuations.transforms import rebuild_query_contract

    return {
        "flow_state": "executing",
        "continuation_type": "time_delta",
        "continuation_delta_type": decision.delta_type or "time",
        "resolver_message": None,
        "query_contract": rebuild_query_contract(
            session_query_contract,
            time_range=resolved_time_range,
            result_limit=decision.result_limit if decision.result_limit is not None else session_query_contract.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None
            else session_query_contract.result_reference,
            continuation_type="time_delta",
            continuation_delta_type=decision.delta_type or "time",
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
            "response": render_message("query.clarify.unsure_rephrase", language),
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
                "response": render_message("query.clarify.unsure_rephrase", language),
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
        query_contract = build_grounded_query_contract(
            query_frames=query_frames,
            frame_ids=frame_ids,
            operation=operation,
        )
        if query_contract is None:
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": render_message("query.clarify.unsure_rephrase", language),
                "flow_state": "parsing",
                "session_active": True,
                "pending_clarification": None,
                "show_expanded": bool(session.get("show_expanded", False)),
                "current_page": session.get("current_page", 0),
            }
        return {
            "flow_state": "executing",
            "resolver_message": None,
            "query_contract": query_contract,
            "current_page": 0,
            "show_expanded": False,
        }

    return None


async def handle_pending_clarification(step: Any, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Resolve a follow-up against an unresolved semantic query state."""
    pending = step._load_pending_clarification(session)
    if pending is None:
        return await parse_new_query(step, state)

    message = state.get("message", "")
    today_state = state.get("today")
    today = today_state if isinstance(today_state, date) else lagos_today()
    locale = LocaleManager.normalize(state.get("language")).value

    deterministic_result = step.parser.parse_deterministic(message, today=today, language=locale)
    if deterministic_result is not None:
        updates = parse_result_to_updates(step, deterministic_result, state=state, today=today, language=locale)
        return step._append_query_session_transition(updates, "replace_session_new_query")

    clarification_time_range = step.parser.parse_clarification_time_range(message, today=today)
    if clarification_time_range is None and _looks_like_explicit_fresh_query_interrupt(message):
        updates = await parse_new_query(step, state)
        return step._append_query_session_transition(updates, "replace_session_new_query")

    decision = await step.reasoner.reason(
        step._build_reasoner_context(
            message=message,
            today=today,
            language=locale,
            pending_clarification=pending,
            query_frames=step._load_query_frames(session),
            state=state,
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
        return await parse_reasoner_extraction_to_updates(
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
        updates = parse_result_to_updates(step, result, state=state, today=today, language=locale)
        updates.update(step._semantic_trace_updates(decision))
        return updates

    updates = await parse_reasoner_extraction_to_updates(
        step,
        decision,
        state=state,
        today=today,
        language=locale,
    )
    updates.update(step._semantic_trace_updates(decision))
    return updates


async def handle_continuation(step: Any, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Handle possible continuation of previous query."""
    from apps.core.src.agent.graphs.query.continuations.transforms import rebuild_query_contract
    from apps.core.src.agent.graphs.query.models import Filters

    message = state.get("message", "")
    today_state = state.get("today")
    today = today_state if isinstance(today_state, date) else lagos_today()
    session_query_contract = step._load_session_query_contract(session)
    locale = LocaleManager.normalize(state.get("language")).value

    items: list[QueryResultItem] = []
    restored_query_result: QueryResult | None = None
    possible_result = session.get("query_result")

    if isinstance(possible_result, QueryResult):
        restored_query_result = possible_result
    elif isinstance(possible_result, dict):
        try:
            restored_query_result = QueryResult.model_validate(possible_result)
        except Exception:
            restored_query_result = None

    if restored_query_result is not None and restored_query_result.items:
        items = restored_query_result.items
    elif possible_result:
        raw_items = []
        if isinstance(possible_result, dict):
            raw_items = possible_result.get("items", [])
        else:
            raw_items = getattr(possible_result, "items", [])
        if raw_items:
            items = [QueryResultItem.model_validate(i) if isinstance(i, dict) else i for i in raw_items]

    surface_view = restored_query_result.surface_view if restored_query_result is not None else None
    logger.info(
        "query_continuation_entry",
        has_query_contract=session_query_contract is not None,
        has_surface=bool(surface_view is not None),
        has_query_result=bool(session.get("query_result")),
        current_page=session.get("current_page", 0),
        show_expanded=bool(session.get("show_expanded", False)),
        surface_type=surface_view.mode.value if surface_view is not None else None,
    )
    query_frames = step._load_query_frames(session)

    decision = await step.reasoner.reason(
        step._build_reasoner_context(
            message=message,
            today=today,
            language=locale,
            query_contract=session_query_contract,
            items=items,
            surface_view=surface_view,
            query_frames=query_frames,
            state=state,
        )
    )
    cont_type = decision.continuation_type or "unclear"

    logger.info(
        "query_continuation_type",
        type=cont_type,
        confidence=decision.confidence,
        reason=decision.reason,
        semantic_decision=decision.decision,
    )
    logger.info(
        "query_continuation_resolution",
        path="semantic_reasoner",
        semantic_decision=decision.decision,
        continuation_type=cont_type,
        followup_intent=decision.followup_intent,
        delta_type=decision.delta_type,
    )

    if decision.decision == "end_session":
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="end_session",
            decision=decision.decision,
        )
        return step._append_query_session_transition(
            {
                "transaction_outcome": TransactionOutcome.OK,
                "response": step._resolve_end_session_response(decision, locale=locale),
                "session_active": False,
                "flow_state": "complete",
                **step._semantic_trace_updates(decision),
            },
            "end_query_session",
        )

    if decision.decision in {"fresh_query", "new_query", "reinterpret_query"}:
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="reparse_query",
            decision=decision.decision,
        )
        logger.info("query_continuation_resolution", path="semantic_reparse", semantic_decision=decision.decision)
        semantic_updates = await parse_reasoner_extraction_to_updates(
            step,
            decision,
            state=state,
            today=today,
            language=locale,
        )
        semantic_updates.update(step._semantic_trace_updates(decision))
        step._append_query_session_transition(semantic_updates, "replace_session_new_query")
        return semantic_updates

    if decision.decision != "continuation":
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="clarify",
            decision=decision.decision,
        )
        logger.info("query_continuation_resolution", path="fallback_clarify_active_session", semantic_decision=decision.decision)
        return step._ambiguous_followup_updates(locale=locale, session=session)

    grounded_updates = resolve_grounded_followup(
        step,
        decision=decision,
        session=session,
        language=locale,
    )
    if grounded_updates is not None and step._should_ignore_grounded_query_for_aggregate(
        grounded_updates=grounded_updates,
        session_query_contract=session_query_contract,
        continuation_type=cont_type,
    ):
        logger.info(
            "query_grounded_followup_ignored",
            reason="aggregate_requires_new_query_shape",
            grounded_intent=grounded_updates["query_contract"].intent.value,
            original_intent=session_query_contract.intent.value if session_query_contract is not None else None,
        )
        grounded_updates = None
    if grounded_updates is not None:
        if decision.answer_mode == "ask_clarify":
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
                trigger_reason="grounded_ask_clarify",
                decision=decision,
                state=state,
                session=session,
                session_query_contract=session_query_contract,
                message=message,
                today=today,
                language=locale,
            )
            if recovered_updates is not None:
                step._log_single_item_followup(
                    surface_view=surface_view,
                    continuation_type="time_delta",
                    followup_outcome="time_rescope_query",
                    decision=decision.decision,
                )
                recovered_updates.update(step._semantic_trace_updates(decision))
                return recovered_updates
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="clarify" if decision.answer_mode == "ask_clarify" else "grounded_answer",
            decision=decision.decision,
        )
        grounded_updates.update(step._semantic_trace_updates(decision))
        return grounded_updates

    followup_intent = decision.followup_intent or "none"
    if followup_intent not in ("refine_existing", "replace_scope", "continue_pagination", "none"):
        followup_intent = "none"

    if decision.confidence is not None and decision.confidence < step._LOW_CONFIDENCE_THRESHOLD:
        if cont_type not in {"drill_down", "recipient_drill_down", "conversational"}:
            supported_query_updates = await maybe_recover_supported_followup_query(
                step,
                state=state,
                today=today,
                language=locale,
                has_original_scope=session_query_contract is not None,
                reasoner_extraction=getattr(decision, "extraction", None),
                reasoner_confidence=decision.confidence,
            )
            if supported_query_updates is not None:
                supported_query_updates.update(step._semantic_trace_updates(decision))
                return supported_query_updates
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
                trigger_reason="low_confidence_unclear",
                decision=decision,
                state=state,
                session=session,
                session_query_contract=session_query_contract,
                message=message,
                today=today,
                language=locale,
            )
            if recovered_updates is not None:
                step._log_single_item_followup(
                    surface_view=surface_view,
                    continuation_type="time_delta",
                    followup_outcome="time_rescope_query",
                    decision=decision.decision,
                )
                recovered_updates.update(step._semantic_trace_updates(decision))
                return recovered_updates
            step._log_single_item_followup(
                surface_view=surface_view,
                continuation_type=cont_type,
                followup_outcome="clarify",
                decision=decision.decision,
            )
            return step._ambiguous_followup_updates(locale=locale, session=session)

    updates: dict[str, Any] = {
        "flow_state": "executing",
        "continuation_type": cont_type,
        "continuation_delta_type": decision.delta_type,
        "resolver_message": None,
        **step._semantic_trace_updates(decision),
    }

    if cont_type == "show_more":
        if session_query_contract is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent == "continue_pagination":
            if session_query_contract.intent != QueryIntent.TRANSACTION_LIST:
                return step._ambiguous_followup_updates(locale=locale, session=session)
            updates["current_page"] = session.get("current_page", 0) + 1
        elif followup_intent == "refine_existing":
            updates["query_contract"] = rebuild_query_contract(
                session_query_contract,
                intent=QueryIntent.TRANSACTION_LIST,
                aggregation=None,
                continuation_type=cont_type,
                continuation_delta_type=decision.delta_type,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False
        else:
            return step._ambiguous_followup_updates(locale=locale, session=session)

    elif cont_type == "time_delta":
        resolved_time_range, clarification_message = await resolve_time_delta_range(
            step,
            decision=decision,
            message=message,
            today=today,
            language=locale,
            state=state,
        )

        if session_query_contract is None or resolved_time_range is None:
            if clarification_message:
                return {
                    "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                    "response": clarification_message,
                    "flow_state": "parsing",
                    "session_active": True,
                    "pending_clarification": None,
                    "show_expanded": bool(session.get("show_expanded", False)),
                    "current_page": session.get("current_page", 0),
                }
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
                trigger_reason="missing_usable_delta",
                decision=decision,
                state=state,
                session=session,
                session_query_contract=session_query_contract,
                message=message,
                today=today,
                language=locale,
            )
            if recovered_updates is not None:
                step._log_single_item_followup(
                    surface_view=surface_view,
                    continuation_type="time_delta",
                    followup_outcome="time_rescope_query",
                    decision=decision.decision,
                )
                recovered_updates.update(step._semantic_trace_updates(decision))
                return recovered_updates
            step._log_single_item_followup(
                surface_view=surface_view,
                continuation_type=cont_type,
                followup_outcome="clarify",
                decision=decision.decision,
            )
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent == "continue_pagination":
            return step._ambiguous_followup_updates(locale=locale, session=session)
        if followup_intent == "none":
            recovered_updates = await maybe_recover_time_rescope_continuation(
                step,
                trigger_reason="missing_usable_delta",
                decision=decision,
                state=state,
                session=session,
                session_query_contract=session_query_contract,
                message=message,
                today=today,
                language=locale,
            )
            if recovered_updates is not None:
                step._log_single_item_followup(
                    surface_view=surface_view,
                    continuation_type="time_delta",
                    followup_outcome="time_rescope_query",
                    decision=decision.decision,
                )
                recovered_updates.update(step._semantic_trace_updates(decision))
                return recovered_updates
            step._log_single_item_followup(
                surface_view=surface_view,
                continuation_type=cont_type,
                followup_outcome="clarify",
                decision=decision.decision,
            )
            return step._ambiguous_followup_updates(locale=locale, session=session)

        updates["query_contract"] = rebuild_query_contract(
            session_query_contract,
            time_range=resolved_time_range,
            result_limit=decision.result_limit if decision.result_limit is not None else session_query_contract.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None
            else session_query_contract.result_reference,
            continuation_type=cont_type,
            continuation_delta_type=decision.delta_type,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False
        step._log_single_item_followup(
            surface_view=surface_view,
            continuation_type=cont_type,
            followup_outcome="time_rescope_query",
            decision=decision.decision,
        )

    elif cont_type == "filter_delta":
        if followup_intent != "refine_existing" or session_query_contract is None:
            return step._ambiguous_followup_updates(locale=locale, session=session)

        delta_type = decision.delta_type
        allow_limit = delta_type in (None, "limit", "reference")
        allow_reference = delta_type in (None, "reference", "limit")

        updates["query_contract"] = rebuild_query_contract(
            session_query_contract,
            filters=decision.filters if decision.filters is not None else session_query_contract.filters,
            merge_filters=decision.filters is not None,
            result_limit=decision.result_limit if decision.result_limit is not None and allow_limit else session_query_contract.result_limit,
            result_reference=decision.result_reference
            if decision.result_reference is not None and allow_reference
            else session_query_contract.result_reference,
            continuation_type=cont_type,
            continuation_delta_type=decision.delta_type,
        )
        updates["current_page"] = 0
        updates["show_expanded"] = False

    elif cont_type == "expand":
        if followup_intent != "refine_existing":
            return step._ambiguous_followup_updates(locale=locale, session=session)
        updates["show_expanded"] = True

    elif cont_type == "conversational":
        return step._append_query_session_transition(
            {
                "transaction_outcome": TransactionOutcome.OK,
                "response": step._compose_conversational_reply(decision, language=locale),
                "session_active": False,
                "flow_state": "complete",
                **step._semantic_trace_updates(decision),
            },
            "exit_query_session_conversational",
        )

    elif cont_type == "drill_down":
        drill_idx = decision.drill_down_index if decision.drill_down_index is not None else 0
        answer_fact_field: Literal["date", "counterparty", "amount", "bank"] | None = None
        if decision.fact_field in {"date", "amount", "bank", "counterparty"}:
            answer_fact_field = cast(Literal["date", "counterparty", "amount", "bank"], decision.fact_field)

        selection_payload = None
        if surface_view is not None and message:
            selection_payload = find_selection_payload(surface_view, label=message)
        if selection_payload is None:
            selection_payload = find_selection_payload(surface_view, index=drill_idx)
        if (
            session_query_contract is not None
            and selection_payload is not None
            and (
                selection_payload.selection_kind == "group_bucket"
                or bool(selection_payload.filters_patch)
                or selection_payload.time_patch is not None
            )
        ):
            updates["query_contract"] = apply_selection_payload_to_query(
                session_query_contract,
                selection_payload,
                fact_field=answer_fact_field if decision.drill_down_action == "answer_fact" else None,
                continuation_type=cont_type,
                continuation_delta_type=decision.delta_type,
            )
            updates["current_page"] = 0
            updates["show_expanded"] = False
            return updates

        if items and 0 <= drill_idx < len(items):
            updates["selected_item_index"] = drill_idx
            if selection_payload is not None:
                updates["selected_payload"] = selection_payload
            updates["drill_down_action"] = decision.drill_down_action
            if decision.fact_field:
                updates["fact_field"] = decision.fact_field
            if decision.drill_down_action == "answer_fact":
                updates["_query_session_transition"] = "answer_fact_active_result"

    elif cont_type == "recipient_drill_down":
        recipient_name = decision.recipient_name
        if recipient_name and session_query_contract is not None:
            recipient_answer_fact_field: Literal["date", "counterparty", "amount", "bank"] | None = None
            if decision.fact_field in {"date", "amount", "bank"}:
                recipient_answer_fact_field = cast(Literal["date", "amount", "bank"], decision.fact_field)
            selection_payload = find_selection_payload(surface_view, label=recipient_name)
            if selection_payload is not None and session_query_contract is not None:
                updates["query_contract"] = apply_selection_payload_to_query(
                    session_query_contract,
                    selection_payload,
                    fact_field=recipient_answer_fact_field,
                    continuation_type=cont_type,
                    continuation_delta_type=decision.delta_type,
                )
            else:
                new_filters = Filters(counterparty=[recipient_name])
                updates["query_contract"] = rebuild_query_contract(
                    session_query_contract,
                    filters=new_filters,
                    merge_filters=True,
                    intent=QueryIntent.TRANSACTION_LIST,
                    aggregation=None,
                    result_limit=None,
                    result_reference=None,
                    answer_fact_field=recipient_answer_fact_field,
                    continuation_type=cont_type,
                    continuation_delta_type=decision.delta_type,
                )
            updates["current_page"] = 0
            updates["show_expanded"] = False

    elif cont_type == "unclear":
        supported_query_updates = await maybe_recover_supported_followup_query(
            step,
            state=state,
            today=today,
            language=locale,
            has_original_scope=session_query_contract is not None,
            reasoner_extraction=getattr(decision, "extraction", None),
            reasoner_confidence=decision.confidence,
        )
        if supported_query_updates is not None:
            supported_query_updates.update(step._semantic_trace_updates(decision))
            return supported_query_updates
        recovered_updates = await maybe_recover_time_rescope_continuation(
            step,
            trigger_reason="unclear_continuation",
            decision=decision,
            state=state,
            session=session,
            session_query_contract=session_query_contract,
            message=message,
            today=today,
            language=locale,
        )
        if recovered_updates is not None:
            recovered_updates.update(step._semantic_trace_updates(decision))
            return recovered_updates
        return step._ambiguous_followup_updates(locale=locale, session=session)

    elif cont_type == "aggregate":
        aggregate_updates = await compile_aggregate_continuation_updates(
            step,
            decision=decision,
            state=state,
            today=today,
            language=locale,
            session_query_contract=session_query_contract,
        )
        if aggregate_updates is not None:
            return aggregate_updates
        return step._ambiguous_followup_updates(locale=locale, session=session)

    return updates


async def parse_new_query(step: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Parse a fresh query."""
    message = state.get("message", "")
    today_state = state.get("today")
    today = today_state if isinstance(today_state, date) else lagos_today()
    language = LocaleManager.normalize(state.get("language")).value

    started_at = perf_counter()
    deterministic_result = step.parser.parse_deterministic(message, today=today, language=language)
    if deterministic_result is not None:
        step._log_query_trace(
            state=state,
            phase="parser_compile",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            outcome=step._resolver_outcome_trace(getattr(deterministic_result, "outcome", None)),
            resolution_source="parser_deterministic",
            llm_calls_used=0,
            single_llm_invariant=True,
        )
        updates = parse_result_to_updates(step, deterministic_result, state=state, today=today, language=language)
        updates.update({"_query_llm_calls_used": 0, "_query_single_llm_invariant": True})
        return updates

    result = await step.parser.parse(message, today=today, language=language)
    step._log_query_trace(
        state=state,
        phase="parser_compile",
        latency_ms=(perf_counter() - started_at) * 1000.0,
        outcome=step._resolver_outcome_trace(getattr(result, "outcome", None)),
        resolution_source="parser_parse",
        llm_calls_used=1,
        single_llm_invariant=True,
    )
    updates = parse_result_to_updates(step, result, state=state, today=today, language=language)
    updates.update({"_query_llm_calls_used": 1, "_query_single_llm_invariant": True})
    return updates


async def parse_reasoner_extraction_to_updates(
    step: Any,
    decision: Any,
    *,
    state: dict[str, Any],
    today: date,
    language: str,
) -> dict[str, Any]:
    """Translate semantic reasoner output into compiler-first query updates."""
    extraction = getattr(decision, "extraction", None)
    confidence = getattr(decision, "confidence", None)
    if extraction is None and getattr(decision, "decision", None) in {"fresh_query", "new_query", "reinterpret_query"}:
        return await parse_new_query(step, state)
    compiler_safe_extraction, compiler_safe_reason = step._compiler_safe_extraction_decision(
        extraction=extraction,
        confidence=confidence,
    )
    started_at = perf_counter()

    if extraction is not None:
        compile_target = extraction
        if not compile_target.raw_query:
            compile_target = compile_target.model_copy(update={"raw_query": state.get("message", "")})
        resolution_source = (
            "reasoner_extraction_compile"
            if compiler_safe_extraction is not None
            else "reasoner_extraction_compile_with_ambiguity"
        )
        if compiler_safe_extraction is None:
            logger.info(
                "query_reasoner_parser_fallback",
                reason=compiler_safe_reason,
                semantic_decision=getattr(decision, "decision", None),
                continuation_type=getattr(decision, "continuation_type", None),
                confidence=confidence,
                path="compile_extraction_despite_ambiguity",
            )
        result = step.parser.compile_reasoner_extraction(compile_target, today=today, language=language)
        step._log_query_trace(
            state=state,
            phase="semantic_compile",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            outcome=step._resolver_outcome_trace(getattr(result, "outcome", None)),
            resolution_source=resolution_source,
            semantic_decision=getattr(decision, "decision", None),
            continuation_type=getattr(decision, "continuation_type", None),
            llm_calls_used=1 if getattr(decision, "semantic_llm_used", False) else 0,
            single_llm_invariant=True,
            reasoner_schema=getattr(decision, "semantic_reasoner_schema", None),
        )
        return parse_result_to_updates(step, result, state=state, today=today, language=language)

    logger.info(
        "query_reasoner_parser_fallback",
        reason=compiler_safe_reason,
        semantic_decision=getattr(decision, "decision", None),
        continuation_type=getattr(decision, "continuation_type", None),
        confidence=confidence,
    )
    step._log_query_trace(
        state=state,
        phase="semantic_compile",
        latency_ms=(perf_counter() - started_at) * 1000.0,
        outcome="clarify",
        resolution_source="missing_reasoner_extraction",
        semantic_decision=getattr(decision, "decision", None),
        continuation_type=getattr(decision, "continuation_type", None),
        llm_calls_used=1 if getattr(decision, "semantic_llm_used", False) else 0,
        single_llm_invariant=True,
        reasoner_schema=getattr(decision, "semantic_reasoner_schema", None),
    )
    return {
        "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
        "response": render_message("query.clarify.unsure_rephrase", language),
        "flow_state": "parsing",
        "session_active": True,
        "pending_clarification": None,
        "show_expanded": False,
        "current_page": 0,
    }


def parse_result_to_updates(
    step: Any,
    result: Any,
    *,
    state: dict[str, Any],
    today: date,
    language: str,
    message_override: str | None = None,
) -> dict[str, Any]:
    """Translate parser outcomes into extraction-step state updates."""

    if result.outcome == ResolverOutcome.NEEDS_INPUT:
        clarify_fallback = render_message("query.clarify.default", language)
        updates = {
            "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
            "response": result.resolver_message or clarify_fallback,
            "session_active": True,
            "pending_clarification": (
                PendingClarificationState.model_validate(result.pending_clarification)
                if result.pending_clarification
                else None
            ),
            "flow_state": "parsing",
        }
        if result.resolver_message:
            updates["resolver_message"] = result.resolver_message
        return updates

    resolver_msg_parts = []
    if result.resolver_message and step._should_attach_resolver_message(result):
        resolver_msg_parts.append(result.resolver_message)

    if result.notices:
        resolver_msg_parts.extend(result.notices)

    resolver_msg = "\n".join(resolver_msg_parts) if resolver_msg_parts else None

    if result.extraction is None:
        return {
            "transaction_outcome": TransactionOutcome.FAILED,
            "response": render_message("query.error.general", language),
            "flow_state": "parsing",
        }

    query_contract = None
    if isinstance(result.query_contract, dict):
        try:
            query_contract = QueryExecutionContract.model_validate(result.query_contract)
        except Exception:
            query_contract = None

    if query_contract is None:
        query_ir = step.parser.build_query_ir_from_extraction(result.extraction, today=today, language=language)
        query_contract = step.parser.build_execution_contract_from_ir(query_ir)

    return {
        "query_contract": query_contract,
        "resolver_message": resolver_msg,
        "flow_state": "executing",
        "current_page": 0,
        "session_active": True,
        "pending_clarification": None,
        "show_expanded": False,
    }
