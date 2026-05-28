"""Compiler path helpers for query extraction and semantic reparse flows."""

from __future__ import annotations

from datetime import date
from time import perf_counter
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.query.compiler.lexical_recovery import looks_like_support_problem_statement
from apps.chat.src.agent.workers.query.models.domain import QueryExecutionContract
from apps.chat.src.agent.workers.query.models.extraction import (
    PendingClarificationState,
    ResolverOutcome,
)
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
    if looks_like_support_problem_statement(str(state.get("message") or "")):
        logger.info(
            "query_reasoner_support_problem_guarded",
            semantic_decision=getattr(decision, "decision", None),
            continuation_type=getattr(decision, "continuation_type", None),
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
    del state, message_override

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
