"""Recovery path for supported query follow-ups after unclear continuation routing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from banking.transactions.query.compiler.lexical_recovery import looks_like_support_problem_statement
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.models.extraction import (
    QueryExtractionResult,
    ResolverOutcome,
    TimeReference,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

ParseResultToUpdates = Callable[..., dict[str, Any]]


async def maybe_recover_supported_followup_query(
    step: Any,
    *,
    state: dict[str, Any],
    today: date,
    language: str,
    has_original_scope: bool = False,
    reasoner_extraction: QueryExtractionResult | None = None,
    reasoner_confidence: float | None = None,
    parse_result_to_updates: ParseResultToUpdates,
) -> dict[str, Any] | None:
    if looks_like_support_problem_statement(str(state.get("message") or "")):
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason="support_problem_signal",
            resolution_source="reasoner_extraction_compile",
        )
        return None

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

    query_request: QueryRequest | dict[str, Any] | None = parsed_result.query_request
    if isinstance(query_request, dict):
        try:
            query_request = QueryRequest.model_validate(query_request)
        except Exception:
            query_request = None
    if not isinstance(query_request, QueryRequest):
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason="missing_query_request",
            resolution_source=resolution_source,
        )
        return None

    if not step._has_supported_followup_query_signal(query_request):
        logger.info(
            "query_continuation_resolution",
            path="fallback_parse_supported_query",
            recovered=False,
            skip_reason="time_only_or_weak_query_signal",
            parsed_intent=query_request.intent.value,
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
            parsed_intent=query_request.intent.value,
            resolution_source=resolution_source,
        )
        return None

    logger.info(
        "query_continuation_resolution",
        path="fallback_parse_supported_query",
        recovered=True,
        parsed_intent=query_request.intent.value,
        resolution_source=resolution_source,
    )
    recovered_updates = parse_result_to_updates(step, parsed_result, state=state, today=today, language=language)
    return step._append_query_session_transition(recovered_updates, "replace_session_new_query")


__all__ = ["maybe_recover_supported_followup_query"]
