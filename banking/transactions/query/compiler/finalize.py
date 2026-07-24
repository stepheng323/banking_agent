"""Finalize parser extraction into parse results."""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any, Literal, cast

from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.compiler import filtering as filter_compiler
from banking.transactions.query.compiler import lexical_recovery
from banking.transactions.query.compiler import operations as operation_compiler
from banking.transactions.query.compiler.resolver import Decision, Prompt, resolve
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import (
    Ambiguity,
    AmbiguityCode,
    FactQueryKind,
    ParserQueryExtraction,
    PendingClarificationState,
    QueryAggregation,
    QueryComparison,
    QueryExtractionResult,
    QueryFilters,
    QueryParseResult,
    QueryRequestShape,
    QueryTimeRange,
    ReasonerQueryExtraction,
    RequestedCapability,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.prompts.main import QUERY_PARSER_PROMPT
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.observability.llm_call_metrics import record_llm_call, structured_output_metrics
from shared.observability.llm_http import start_llm_http_recording, stop_llm_http_recording, summarize_llm_http_records
from shared.observability.llm_provider_metadata import extract_provider_llm_metadata
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def build_pending_clarification(
    parser: Any,
    *,
    extraction: QueryExtractionResult,
    language: str,
    message: str | None,
    resolver_message: str | None,
) -> PendingClarificationState:
    ambiguity_codes = {ambiguity.code for ambiguity in extraction.ambiguities}
    clarification_type: (
        Literal["time", "selection", "recipient", "account", "direction", "category", "status", "amount", "scope"]
        | None
    ) = None
    target_field = None
    if AmbiguityCode.RECIPIENT_VAGUE in ambiguity_codes:
        clarification_type, target_field = "recipient", "recipient"
    elif AmbiguityCode.TIME_VAGUE in ambiguity_codes:
        clarification_type, target_field = "time", "time_range"
    elif AmbiguityCode.AMOUNT_VAGUE in ambiguity_codes:
        clarification_type, target_field = "amount", "amount_range"
    return PendingClarificationState(
        original_query=message or extraction.raw_query or "",
        current_intent=extraction.intent,
        original_extraction=extraction.model_copy(deep=True),
        ambiguities=list(extraction.ambiguities),
        resolver_message=resolver_message,
        language=language,
        clarification_type=clarification_type,
        target_field=target_field,
    )


def looks_like_vague_time_phrase(raw_query: str) -> str | None:
    normalized = " ".join((raw_query or "").strip().lower().split())
    if not normalized:
        return None
    for phrase in (
        "recently",
        "sometime ago",
        "a while back",
        "some time ago",
        "lately",
        "these days",
        "last",
    ):
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            return phrase
    return None


def derive_ambiguities(extraction: QueryExtractionResult) -> list[Ambiguity]:
    ambiguities: list[Ambiguity] = []
    if extraction.time_range.reference_type == TimeReference.VAGUE:
        context = (
            extraction.time_range.period or looks_like_vague_time_phrase(extraction.raw_query or "") or "that time"
        )
        ambiguities.append(Ambiguity(code=AmbiguityCode.TIME_VAGUE, context=context))
    return ambiguities


def derive_requested_capabilities(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    intent: QueryIntent,
) -> list[RequestedCapability]:
    requested_capabilities: list[RequestedCapability] = []

    def _add(capability: RequestedCapability) -> None:
        if capability not in requested_capabilities:
            requested_capabilities.append(capability)

    inferred_transaction_type = filter_compiler.infer_transaction_type(
        extracted_transaction_type=extraction.filters.transaction_type,
        raw_query=extraction.raw_query,
        intent=intent,
    )

    if extraction.filters.recipient:
        _add(RequestedCapability.FILTER_RECIPIENT)
    if extraction.filters.min_amount is not None or extraction.filters.max_amount is not None:
        _add(RequestedCapability.FILTER_AMOUNT)
    if extraction.filters.category:
        _add(RequestedCapability.FILTER_CATEGORY)
    if extraction.filters.bank:
        _add(RequestedCapability.FILTER_BANK)
    if extraction.filters.narration_keyword:
        _add(RequestedCapability.SEARCH_NARRATION_KEYWORD)
    if inferred_transaction_type is not None:
        _add(RequestedCapability.FILTER_TX_TYPE)

    if extraction.time_range.reference_type == TimeReference.ALL_TIME:
        _add(RequestedCapability.TIME_ALL)
    elif extraction.time_range.reference_type in {TimeReference.EXPLICIT, TimeReference.VAGUE}:
        _add(RequestedCapability.TIME_RELATIVE)

    if intent == QueryIntent.TIME_COMPARISON:
        _add(RequestedCapability.TIME_COMPARISON)

    if intent == QueryIntent.ANALYTICS_SUMMARY:
        if extraction.aggregation and extraction.aggregation.type == "breakdown":
            _add(RequestedCapability.AGGREGATE_GROUP)
        else:
            _add(RequestedCapability.AGGREGATE_SUM)

    if intent == QueryIntent.BENEFICIARY_SUMMARY:
        _add(RequestedCapability.AGGREGATE_GROUP)

    return requested_capabilities


def inflate_parser_extraction(
    parser: Any,
    extraction: QueryExtractionResult | ParserQueryExtraction,
    *,
    question: str,
    language: str,
) -> QueryExtractionResult:
    if isinstance(extraction, QueryExtractionResult):
        inflated = extraction.model_copy(deep=True)
    else:
        inflated = QueryExtractionResult(
            intent=extraction.intent,
            filters=extraction.filters.model_copy(deep=True),
            time_range=extraction.time_range.model_copy(deep=True),
            comparison=extraction.comparison.model_copy(deep=True) if extraction.comparison is not None else None,
            aggregation=extraction.aggregation.model_copy(deep=True) if extraction.aggregation is not None else None,
            request_shape=extraction.request_shape,
            fact_query_kind=extraction.fact_query_kind,
            result_limit=extraction.result_limit,
            result_reference=extraction.result_reference,
            answer_fact_field=extraction.answer_fact_field,
            insight=extraction.insight.model_copy(deep=True) if extraction.insight is not None else None,
        )

    inflated.raw_query = question
    inflated = parser._recover_known_fragile_query_shapes(inflated, language=language)
    if inflated.request_shape is None:
        inflated.request_shape = parser._derive_request_shape(inflated)
    if inflated.fact_query_kind is None:
        inflated.fact_query_kind = parser._derive_fact_query_kind(inflated)
    if not inflated.requested_capabilities:
        inflated.requested_capabilities = parser._derive_requested_capabilities(
            inflated,
            intent=inflated.intent,
        )
    if not inflated.ambiguities:
        inflated.ambiguities = parser._derive_ambiguities(inflated)
    return inflated


def derive_request_shape(extraction: QueryExtractionResult) -> QueryRequestShape | None:
    if extraction.request_shape is not None:
        return extraction.request_shape

    if extraction.fact_query_kind is not None or extraction.answer_fact_field is not None:
        return QueryRequestShape.FACT

    if extraction.intent == QueryIntent.TIME_COMPARISON:
        return QueryRequestShape.COMPARISON
    if extraction.intent == QueryIntent.AFFORDABILITY:
        return QueryRequestShape.AFFORDABILITY
    if extraction.intent == QueryIntent.INSIGHT:
        return QueryRequestShape.INSIGHT
    if extraction.intent == QueryIntent.BENEFICIARY_SUMMARY:
        return QueryRequestShape.GROUPED_SUMMARY
    if extraction.intent == QueryIntent.ANALYTICS_SUMMARY:
        return QueryRequestShape.ANALYTICS
    if extraction.intent in (QueryIntent.TRANSACTION_DETAIL, QueryIntent.TRANSACTION_SEARCH):
        return QueryRequestShape.DETAIL
    if extraction.intent == QueryIntent.TRANSACTION_LIST:
        return QueryRequestShape.LIST
    return None


def derive_fact_query_kind(extraction: QueryExtractionResult) -> FactQueryKind | None:
    if extraction.fact_query_kind is not None:
        return extraction.fact_query_kind
    if extraction.answer_fact_field in {
        "date",
        "counterparty",
        "amount",
        "bank",
        "status",
        "description",
        "reference",
        "account",
        "direction",
        "category",
    }:
        return FactQueryKind(extraction.answer_fact_field)
    return None


def render_resolver_prompt_message(prompt: Prompt, language: str) -> str:
    if prompt.key == "query.time_vague":
        return render_message(
            "query.clarify.time_vague",
            language,
            {
                "context": prompt.vars.get("context") or "that time",
                "suggestion": prompt.vars.get("suggestion") or "last 30 days",
            },
            fallback_en="What time period did you mean by '{context}'? You can say something like '{suggestion}'.",
        )
    return render_message(
        cast(MessageKey, prompt.key),
        language,
        prompt.vars,
        fallback_en=str(prompt.vars.get("context") or render_message("query.clarify.default", language)),
    )


def finalize_extraction(
    parser: Any,
    extraction: QueryExtractionResult,
    *,
    today: Any,
    language: str,
) -> QueryParseResult:
    extraction = extraction.model_copy(deep=True)
    extraction = parser._normalize_month_name_without_year(extraction)
    extraction = operation_compiler.normalize_query_extraction(extraction)
    parser._validate_capabilities(extraction)
    decision = resolve(extraction, language=language)

    outcome = ResolverOutcome.OK
    message = None
    notices = []
    pending_clarification: PendingClarificationState | None = None

    if decision.decision == Decision.ASK_CLARIFY:
        outcome = ResolverOutcome.NEEDS_INPUT
        message = (
            parser._render_resolver_prompt_message(decision.prompts[0], language)
            if decision.prompts
            else render_message("query.clarify.default", language)
        )
        pending_clarification = parser._build_pending_clarification(
            extraction=decision.extraction,
            language=language,
            message=extraction.raw_query,
            resolver_message=message,
        )
    elif decision.decision == Decision.NEGOTIATE and decision.negotiation:
        outcome = ResolverOutcome.NEGOTIATED
        message = decision.negotiation.message

    if decision.clamped.days_back:
        notices.append(
            render_message(
                "query.notice.clamped_days",
                language,
                {"days_back": decision.clamped.days_back},
            )
        )

    if parser._requires_time_comparison_period(decision.extraction):
        clarify_message = render_message("query.time_comparison.prompt_specify_period", language)
        pending_clarification = parser._build_pending_clarification(
            extraction=decision.extraction,
            language=language,
            message=decision.extraction.raw_query,
            resolver_message=clarify_message,
        )
        return QueryParseResult(
            outcome=ResolverOutcome.NEEDS_INPUT,
            extraction=decision.extraction,
            resolver_message=clarify_message,
            notices=notices,
            pending_clarification=pending_clarification.model_dump(),
            patch={},
        )

    query_request = parser.build_query_request_from_extraction(decision.extraction, today=today, language=language)

    return QueryParseResult(
        outcome=outcome,
        extraction=decision.extraction,
        query_request=query_request.model_dump(mode="json"),
        resolver_message=message,
        notices=notices,
        pending_clarification=pending_clarification.model_dump() if pending_clarification else None,
        patch={},
    )


def parse_deterministic(
    parser: Any,
    question: str,
    *,
    today: Any,
    language: str = "en",
) -> QueryParseResult | None:
    normalized = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.!,")
    if not normalized:
        return None

    came_in_match = re.fullmatch(
        r"(?:how much|what amount|total)\s+(?:came in|come in|entered|was received|did i receive)"
        r"(?:(?:\s+(?:in|for|during|over)\s+|\s+)(.+))?",
        normalized,
    )
    if came_in_match:
        explicit_time_text = came_in_match.group(1)
        time_range = (
            parser._extract_relative_time_range_from_query(question)
            if explicit_time_text
            else QueryTimeRange(reference_type=TimeReference.UNSPECIFIED)
        )
        if time_range is None:
            time_range = QueryTimeRange(reference_type=TimeReference.UNSPECIFIED)
        extraction = QueryExtractionResult(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            raw_query=question,
            time_range=time_range,
            filters=QueryFilters(transaction_type="credit"),
            aggregation=QueryAggregation(type="sum"),
            request_shape=QueryRequestShape.ANALYTICS,
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    affordability_match = re.fullmatch(
        r"(?:can|could)\s+i\s+(?:afford|send|transfer|pay|spend|cover)\s+"
        r"(?:₦|ngn\s*)?(\d[\d,]*(?:\.\d+)?)\s*(k|m|naira|ngn)?",
        normalized,
    )
    if affordability_match:
        amount = operation_compiler._parse_affordability_amount(  # type: ignore[attr-defined]
            affordability_match.group(1),
            affordability_match.group(2),
        )
        extraction = QueryExtractionResult(
            intent=QueryIntent.AFFORDABILITY,
            raw_query=question,
            filters=QueryFilters(min_amount=amount, max_amount=amount),
            request_shape=QueryRequestShape.AFFORDABILITY,
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    cashflow_by_account_match = re.fullmatch(
        r"(?:(?:break\s*down|breakdown|show|list)\s+)?(?:my\s+)?cash\s*flow\s+by\s+(?:account|bank)",
        normalized,
    )
    if cashflow_by_account_match:
        extraction = QueryExtractionResult(
            intent=QueryIntent.CASH_FLOW_SUMMARY,
            raw_query=question,
            time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
            aggregation=QueryAggregation(type="breakdown", group_by="account"),
            request_shape=QueryRequestShape.ANALYTICS,
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    month_comparison_match = re.fullmatch(
        r"(?:did\s+i\s+)?(?:spend|spent)\s+more\s+"
        r"(this month|last month)\s+than\s+(this month|last month)",
        normalized,
    )
    if month_comparison_match:
        primary_period, comparison_period = month_comparison_match.groups()
        extraction = QueryExtractionResult(
            intent=QueryIntent.TIME_COMPARISON,
            raw_query=question,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period=primary_period.replace(" ", "_")),
            comparison=QueryComparison(mode="explicit_period", period=comparison_period.replace(" ", "_")),
            filters=QueryFilters(transaction_type="debit"),
            request_shape=QueryRequestShape.COMPARISON,
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    same_period_last_month_match = re.fullmatch(
        r"compare\s+(?:my\s+)?(?:spending\s+)?this month\s+"
        r"(?:with|to|vs|versus)\s+(?:the\s+)?same period last month",
        normalized,
    )
    if same_period_last_month_match:
        extraction = QueryExtractionResult(
            intent=QueryIntent.TIME_COMPARISON,
            raw_query=question,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
            comparison=QueryComparison(mode="explicit_period", period="same_period_last_month"),
            filters=QueryFilters(transaction_type="debit"),
            request_shape=QueryRequestShape.COMPARISON,
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    latest_status_match = re.fullmatch(
        r"(?:(?:what(?:'s| is)|whats|tell me|check|show|get)\s+)?"
        r"(?:the\s+)?status\s+of\s+(?:my\s+)?(?:last|latest|most recent)\s+"
        r"(?:transaction|transfer|payment)"
        r"|(?:(?:what(?:'s| is)|whats)\s+)?(?:my\s+)?(?:last|latest|most recent)\s+"
        r"(?:transaction|transfer|payment)\s+status",
        normalized,
    )
    if latest_status_match:
        extraction = QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_DETAIL,
            raw_query=question,
            time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
            request_shape=QueryRequestShape.FACT,
            fact_query_kind=FactQueryKind.STATUS,
            result_limit=1,
            result_reference="latest",
            answer_fact_field="status",
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    status_list_match = re.fullmatch(
        r"(?:(?:show|list|view|get|check|display|see)\s+)?(?:my\s+)?"
        r"(failed|failure|declined|rejected|pending|processing|successful|success|posted|completed|reversed|refunded)\s+"
        r"(?:transactions?|transfers?|payments?)"
        r"(?:\s+(?:in|for|during|over)\s+(.+))?",
        normalized,
    )
    if status_list_match:
        status_text, explicit_time_text = status_list_match.groups()
        status = filter_compiler.infer_status_filter(extracted_status=status_text, raw_query=question)
        time_range = (
            parser._extract_relative_time_range_from_query(question)
            if explicit_time_text
            else QueryTimeRange(reference_type=TimeReference.UNSPECIFIED)
        )
        if time_range is None:
            time_range = QueryTimeRange(reference_type=TimeReference.UNSPECIFIED)
        extraction = QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            raw_query=question,
            time_range=time_range,
            filters=QueryFilters(status=status),
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    recent_list_match = re.fullmatch(
        r"(?:(?:show|list|view|get|check|display|see)\s+)?"
        r"(?:(?:my|all my|all)\s+)?recent\s+(transactions?|debits?|credits?|payments?)"
        r"(?:\s+(?:in|for|during|over)\s+(.+))?",
        normalized,
    )
    if recent_list_match:
        noun, explicit_time_text = recent_list_match.groups()
        tx_type: Literal["credit", "debit"] | None = None
        if noun.startswith("debit") or noun.startswith("payment"):
            tx_type = "debit"
        elif noun.startswith("credit"):
            tx_type = "credit"
        time_range = (
            parser._extract_relative_time_range_from_query(question)
            if explicit_time_text
            else QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="recent_30_days", days_back=29)
        )
        if time_range is None:
            time_range = QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="recent_30_days", days_back=29)
        extraction = QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            raw_query=question,
            time_range=time_range,
            filters=QueryFilters(transaction_type=tx_type),
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    day_scoped_list_match = re.fullmatch(
        r"(?:(?:show|list|view|get|check|display|see)\s+)?"
        r"(?:(?:my|all my|all)\s+)?"
        r"(today|today's|yesterday|yesterday's|this week|this week's|last week|last week's|"
        r"this month|this month's|last month|last month's|this year|this year's|last year|last year's)\s+"
        r"(transactions?|transaction|debits?|credits?|payments?)",
        normalized,
    )
    if day_scoped_list_match:
        period_phrase, noun = day_scoped_list_match.groups()
        normalized_period = period_phrase.replace("'s", "").replace(" ", "_")
        tx_type = None
        if noun.startswith("debit") or noun.startswith("payment"):
            tx_type = "debit"
        elif noun.startswith("credit"):
            tx_type = "credit"
        extraction = QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            raw_query=question,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period=normalized_period),
            filters=QueryFilters(transaction_type=tx_type),
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    if re.fullmatch(
        r"(?:(?:show|list|view|get)\s+)?(?:my\s+)?(?:transactions?|transaction\s+history|history|statement)", normalized
    ):
        extraction = QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            raw_query=question,
            time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    match = re.fullmatch(r"(?:(?:show|list|view|get)\s+)?(?:my\s+)?last\s+(\d+)\s+transactions?", normalized)
    if match:
        limit = int(match.group(1))
        extraction = QueryExtractionResult(
            intent=QueryIntent.TRANSACTION_LIST,
            raw_query=question,
            time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
            result_limit=limit,
            result_reference="latest",
        )
        return parser._finalize_extraction(extraction, today=today, language=language)

    return None


async def parse(parser: Any, question: str, today: Any, language: str = "en") -> QueryParseResult:
    if lexical_recovery.looks_like_support_problem_statement(question):
        logger.info("query_parser_support_problem_guarded")
        return QueryParseResult(
            outcome=ResolverOutcome.NEEDS_INPUT,
            extraction=QueryExtractionResult(raw_query=question),
            resolver_message=render_message("query.clarify.unsure_rephrase", language),
        )

    deterministic = parse_deterministic(parser, question, today=today, language=language)
    if deterministic is not None:
        return deterministic

    prompt = QUERY_PARSER_PROMPT.format(today=today.isoformat(), question=question)
    structured_llm = cast(Any, parser.llm).with_structured_output(ParserQueryExtraction)

    try:
        started_at = perf_counter()
        http_recording_token = start_llm_http_recording()
        try:
            raw_extraction = await ainvoke_with_config(
                structured_llm,
                prompt,
                config=build_llm_runnable_config(
                    role="query_parser",
                    task_domain="query",
                    locale=language,
                    extra_metadata={"prompt_chars": len(prompt)},
                )
                or None,
            )
        finally:
            http_metrics = summarize_llm_http_records(stop_llm_http_recording(http_recording_token))
        duration_ms = (perf_counter() - started_at) * 1000.0
        logger.info(
            "query_parser_llm_call",
            duration_ms=round(duration_ms, 2),
            prompt_chars=len(prompt),
            language=language,
            **http_metrics,
            **extract_provider_llm_metadata(raw_extraction),
        )
        model = getattr(parser.llm, "model_name", None) or getattr(parser.llm, "model", None)
        output_metrics = structured_output_metrics(raw_extraction)
        record_llm_call(
            event_name="query_parser_llm_call",
            duration_ms=duration_ms,
            model=model,
            response_type=ParserQueryExtraction.__name__,
            system_chars=len(prompt),
            user_chars=0,
            output_json_chars=int(output_metrics["output_json_chars"]),
            output_token_estimate=int(output_metrics["output_token_estimate"]),
            extra_fields={
                "language": language,
                **output_metrics,
                **http_metrics,
                **extract_provider_llm_metadata(raw_extraction),
            },
        )
        extraction = parser._inflate_parser_extraction(raw_extraction, question=question, language=language)
        return parser._finalize_extraction(extraction, today=today, language=language)
    except Exception as e:
        logger.error("parse_error", error=str(e))

    return QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=QueryExtractionResult(raw_query=question),
    )


def resolve_existing_extraction(
    parser: Any, extraction: QueryExtractionResult, *, today: Any, language: str
) -> QueryParseResult:
    return parser._finalize_extraction(extraction, today=today, language=language)


def compile_extraction(
    parser: Any, extraction: QueryExtractionResult, *, today: Any, language: str
) -> QueryParseResult:
    return parser.resolve_existing_extraction(extraction, today=today, language=language)


def compile_reasoner_extraction(
    parser: Any,
    extraction: QueryExtractionResult | ReasonerQueryExtraction,
    *,
    today: Any,
    language: str,
) -> QueryParseResult:
    full_extraction = (
        extraction.to_query_extraction_result()
        if isinstance(extraction, ReasonerQueryExtraction)
        else extraction.model_copy(deep=True)
    )
    return parser.compile_extraction(full_extraction, today=today, language=language)


def validate_capabilities(extraction: QueryExtractionResult) -> None:
    if not extraction.filters.narration_keyword:
        extraction.requested_capabilities = [
            capability
            for capability in extraction.requested_capabilities
            if capability
            not in {RequestedCapability.SEARCH_NARRATION_KEYWORD, RequestedCapability.SEARCH_NARRATION_FUZZY}
        ]

    if extraction.filters.min_amount is not None or extraction.filters.max_amount is not None:
        if RequestedCapability.FILTER_AMOUNT not in extraction.requested_capabilities:
            extraction.requested_capabilities.append(RequestedCapability.FILTER_AMOUNT)

    if extraction.filters.recipient:
        if RequestedCapability.FILTER_RECIPIENT not in extraction.requested_capabilities:
            extraction.requested_capabilities.append(RequestedCapability.FILTER_RECIPIENT)

    if extraction.filters.category:
        if RequestedCapability.FILTER_CATEGORY not in extraction.requested_capabilities:
            extraction.requested_capabilities.append(RequestedCapability.FILTER_CATEGORY)

    if extraction.time_range.reference_type == TimeReference.ALL_TIME:
        if RequestedCapability.TIME_ALL not in extraction.requested_capabilities:
            extraction.requested_capabilities.append(RequestedCapability.TIME_ALL)
    elif RequestedCapability.TIME_ALL in extraction.requested_capabilities:
        extraction.requested_capabilities.remove(RequestedCapability.TIME_ALL)


def requires_time_comparison_period(extraction: QueryExtractionResult) -> bool:
    return (
        extraction.intent == QueryIntent.TIME_COMPARISON
        and extraction.time_range.reference_type == TimeReference.UNSPECIFIED
    )
