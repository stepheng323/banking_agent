"""Compiler path helpers for query extraction and semantic reparse flows."""

from __future__ import annotations

import re
from datetime import date
from time import perf_counter
from typing import Any

from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.compiler.lexical_recovery import looks_like_support_problem_statement
from banking.transactions.query.continuations.beneficiary_grounding import (
    ground_unique_saved_recipient,
    recipient_clarification_candidates,
)
from banking.transactions.query.continuations.clarification_state import build_selection_clarification_updates
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import (
    ClarificationOperation,
    InsightSpec,
    PendingClarificationState,
    QueryExtractionResult,
    QueryRequestShape,
    ResolverOutcome,
)
from banking.transactions.query.models.operations import QueryRequest
from banking.transactions.query.plan_compiler import QueryPlanCompileError, compile_query_plan_result
from banking.transactions.query.utils.timezone import lagos_today
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_QUERY_INSIGHT_TYPES = {
    "variance_drivers",
    "probable_duplicates",
    "recurring_patterns",
    "anomalies",
    "counterparty_concentration",
    "forecast",
    "runway",
    "cash_flow_quality",
}

# Recipient-intent phrasing that the parser handles with a BENEFICIARY_SUMMARY intent.
# The router's coarse "counterparty_concentration" hint must not clobber these.
_SEND_TO_RECIPIENT_RE = re.compile(
    r"\b(?:send|sent|transfer|transferred|pay|paid)\b.*\b(?:to|for)\b",
    re.IGNORECASE,
)


def _apply_router_insight_hint(step: Any, result: Any, state: dict[str, Any], *, today: date, language: str) -> Any:
    """Apply the semantic router's typed insight subtype before compilation.

    The router has already spent the turn's first semantic call and identified
    the analytical family.  This adapter prevents a narrower parser mistake
    from silently turning that request into a generic cash-flow surface.
    """
    hint = state.get("query_insight_type")
    extraction = getattr(result, "extraction", None)
    if hint not in _QUERY_INSIGHT_TYPES or not isinstance(extraction, QueryExtractionResult):
        return result
    logger.info(
        "query_insight_hint_received",
        insight_type=hint,
        parser_intent=extraction.intent.value,
    )
    current = extraction.insight
    insight = (
        current
        if current is not None and current.insight_type == hint
        else InsightSpec(insight_type=hint)
    )
    if (
        extraction.intent == QueryIntent.INSIGHT
        and extraction.request_shape == QueryRequestShape.INSIGHT
        and extraction.insight == insight
    ):
        return result
    # Never let a coarse concentration hint override a confident recipient-ranking intent.
    # "Who did I send money to the most" is a beneficiary summary, not spending concentration.
    if hint == "counterparty_concentration" and (
        extraction.intent == QueryIntent.BENEFICIARY_SUMMARY
        or _SEND_TO_RECIPIENT_RE.search(extraction.raw_query or "")
    ):
        logger.info(
            "query_insight_hint_rejected",
            insight_type=hint,
            parser_intent=extraction.intent.value,
            reason="send_to_recipient_intent",
        )
        return result
    prior_intent = extraction.intent.value
    hinted_extraction = extraction.model_copy(
        deep=True,
        update={
            "intent": QueryIntent.INSIGHT,
            "request_shape": QueryRequestShape.INSIGHT,
            "insight": insight,
            "aggregation": None,
            "comparison": None,
            "fact_query_kind": None,
            "answer_fact_field": None,
            "result_reference": None,
        },
    )
    logger.info(
        "query_insight_hint_applied",
        insight_type=hint,
        prior_intent=prior_intent,
    )
    return step.parser.compile_extraction(hinted_extraction, today=today, language=language)


async def parse_new_query(step: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Parse a fresh query."""
    message = state.get("message", "")
    today_state = state.get("today")
    today = today_state if isinstance(today_state, date) else lagos_today()
    language = LocaleManager.normalize(state.get("language")).value

    started_at = perf_counter()
    deterministic_result = step.parser.parse_deterministic(message, today=today, language=language)
    if deterministic_result is not None:
        deterministic_result = _apply_router_insight_hint(
            step,
            deterministic_result,
            state,
            today=today,
            language=language,
        )
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

    raw_preferences = state.get("query_preferences")
    parser_preferences = None
    if isinstance(raw_preferences, dict):
        parser_preferences = {
            key: raw_preferences[key]
            for key in (
                "default_shape",
                "relative_period_mode",
                "default_activity_measure",
                "default_status_inclusion",
            )
            if raw_preferences.get(key) is not None
        } or None
        if raw_preferences.get("default_account_refs"):
            parser_preferences = {
                **(parser_preferences or {}),
                "default_account_scope_available": True,
            }
    if parser_preferences is None:
        result = await step.parser.parse(message, today=today, language=language)
    else:
        result = await step.parser.parse(
            message,
            today=today,
            language=language,
            query_preferences=parser_preferences,
        )
    result = _apply_router_insight_hint(step, result, state, today=today, language=language)
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
    plan_draft = getattr(decision, "plan", None)
    confidence = getattr(decision, "confidence", None)
    started_at = perf_counter()
    if plan_draft is not None:
        try:
            result = compile_query_plan_result(
                step.parser,
                plan_draft,
                today=today,
                language=language,
                raw_query=str(state.get("message") or ""),
            )
        except QueryPlanCompileError:
            logger.info(
                "query_reasoner_plan_rejected",
                semantic_decision=getattr(decision, "decision", None),
                continuation_type=getattr(decision, "continuation_type", None),
            )
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": render_message("query.clarify.unsure_rephrase", language),
                "flow_state": "parsing",
                "session_active": True,
                "_query_reasoner_to_parser_suppressed": True,
            }
        step._log_query_trace(
            state=state,
            phase="semantic_compile",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            outcome="OK",
            resolution_source="reasoner_plan_compile",
            semantic_decision=getattr(decision, "decision", None),
            continuation_type=getattr(decision, "continuation_type", None),
            llm_calls_used=1 if getattr(decision, "semantic_llm_used", False) else 0,
            single_llm_invariant=True,
            reasoner_schema=getattr(decision, "semantic_reasoner_schema", None),
        )
        return parse_result_to_updates(step, result, state=state, today=today, language=language)
    if (
        extraction is None
        and getattr(decision, "semantic_llm_used", False)
        and getattr(decision, "decision", None) in {"fresh_query", "new_query", "reinterpret_query"}
    ):
        # An active-query reasoner has already consumed this turn's sole LLM
        # budget.  Re-parsing raw text here both adds latency and lets an
        # incomplete semantic decision silently change the user's query.
        logger.info(
            "query_reasoner_to_parser_suppressed",
            semantic_decision=getattr(decision, "decision", None),
            reason="missing_reasoner_extraction",
        )
        step._log_query_trace(
            state=state,
            phase="semantic_compile",
            latency_ms=(perf_counter() - started_at) * 1000.0,
            outcome="clarify",
            resolution_source="reasoner_to_parser_suppressed",
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
            "_query_reasoner_to_parser_suppressed": True,
        }
    compiler_safe_extraction, compiler_safe_reason = step._compiler_safe_extraction_decision(
        extraction=extraction,
        confidence=confidence,
    )
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

    if not getattr(decision, "semantic_llm_used", False):
        # A deterministic continuation marker has not spent the turn's model
        # budget.  Preserve the established fresh-query behavior, including
        # explicit-period scope replacement, by letting the parser own it.
        return await parse_new_query(step, state)

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
    del message_override
    raw_preferences = state.get("query_preferences")
    prefer_detailed = (
        isinstance(raw_preferences, dict) and raw_preferences.get("presentation_detail") == "detailed"
    )

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

    query_request = None
    if isinstance(result.query_request, dict):
        try:
            query_request = QueryRequest.model_validate(result.query_request)
        except Exception:
            query_request = None
    if query_request is None:
        query_request = step.parser.build_query_request_from_extraction(
            result.extraction,
            today=today,
            language=language,
        )
    preferred_account_ids: list[str] | None = None
    raw_preferences = state.get("query_preferences")
    if (
        result.extraction.use_default_account_scope
        and isinstance(raw_preferences, dict)
        and isinstance(raw_preferences.get("default_account_refs"), list)
    ):
        preferred_account_ids = [
            str(account_id)
            for account_id in raw_preferences["default_account_refs"]
            if isinstance(account_id, str) and account_id
        ] or None

    raw_beneficiaries = state.get("beneficiaries")
    beneficiaries: list[Any] = raw_beneficiaries if isinstance(raw_beneficiaries, list) else []
    query_request = ground_unique_saved_recipient(query_request, beneficiaries)
    candidates = recipient_clarification_candidates(query_request, beneficiaries)
    if candidates:
        raw_session = state.get("query_session")
        session: dict[str, Any] = raw_session if isinstance(raw_session, dict) else {}
        return build_selection_clarification_updates(
            candidates=candidates,
            operation=ClarificationOperation(grounded_operation="recipient_filter"),
            query_request=query_request,
            locale=language,
            session=session,
            turn_id=state.get("turn_id"),
        )

    updates = {
        "query_request": query_request,
        "execution_contract": result.execution_contract,
        "execute_query_plan": bool(result.execution_contract),
        "resolver_message": resolver_msg,
        "flow_state": "executing",
        "current_page": 0,
        "session_active": True,
        "pending_clarification": None,
        "show_expanded": prefer_detailed,
    }
    if preferred_account_ids is not None:
        updates["account_ids"] = preferred_account_ids
    return updates
