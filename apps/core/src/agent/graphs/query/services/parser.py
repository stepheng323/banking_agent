"""Query parsing service - extracts QueryIR/QueryExecutionContract from natural language."""

from datetime import date
from typing import Any, Literal

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.compiler import finalize as finalize_compiler
from apps.core.src.agent.graphs.query.compiler import lexical_recovery, query_compiler
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    ComparisonDirective,
    ExtractionIntent,
    FactQueryKind,
    Filters,
    ParserQueryExtraction,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryIR,
    QueryOperation,
    QueryParseResult,
    QueryRequestShape,
    QueryTimeRange,
    ReasonerQueryExtraction,
    TimeRange,
)
from apps.core.src.agent.graphs.query.services.resolver import Prompt
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_THIS_YEAR_TOKENS = {"this_year", "current_year", "thisyear", "currentyear"}
_LAST_YEAR_TOKENS = {"last_year", "previous_year", "lastyear", "previousyear"}


class QueryParser:
    """Parse natural language financial questions into QueryExecutionContract."""

    _COUNTERPARTY_PLACEHOLDERS = frozenset(
        {
            "unknown",
            "someone",
            "somebody",
            "person",
            "recipient",
            "sender",
            "merchant",
        }
    )

    def __init__(self, llm: Runnable):
        self.llm = llm

    def _build_pending_clarification(
        self,
        *,
        extraction: QueryExtractionResult,
        language: str,
        message: str | None,
        resolver_message: str | None,
    ):
        return finalize_compiler.build_pending_clarification(
            self,
            extraction=extraction,
            language=language,
            message=message,
            resolver_message=resolver_message,
        )

    def _finalize_extraction(
        self,
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        return finalize_compiler.finalize_extraction(
            self,
            extraction,
            today=today,
            language=language,
        )

    @staticmethod
    def _looks_like_vague_time_phrase(raw_query: str) -> str | None:
        return finalize_compiler.looks_like_vague_time_phrase(raw_query)

    def _derive_ambiguities(
        self,
        extraction: QueryExtractionResult,
    ):
        return finalize_compiler.derive_ambiguities(self, extraction)

    def _derive_requested_capabilities(
        self,
        extraction: QueryExtractionResult,
        *,
        effective_intent: ExtractionIntent,
    ):
        return finalize_compiler.derive_requested_capabilities(
            self,
            extraction,
            effective_intent=effective_intent,
        )

    @staticmethod
    def _derive_request_shape(extraction: QueryExtractionResult) -> QueryRequestShape | None:
        return finalize_compiler.derive_request_shape(extraction)

    @staticmethod
    def _derive_fact_query_kind(extraction: QueryExtractionResult) -> FactQueryKind | None:
        return finalize_compiler.derive_fact_query_kind(extraction)

    def _inflate_parser_extraction(
        self,
        extraction: QueryExtractionResult | ParserQueryExtraction,
        *,
        question: str,
        language: str = "en",
    ) -> QueryExtractionResult:
        return finalize_compiler.inflate_parser_extraction(self, extraction, question=question, language=language)

    @staticmethod
    def _month_token(period: str | None) -> int | None:
        return lexical_recovery.month_token(period)

    @staticmethod
    def _resolve_month_period_with_year_hint(period: str, *, today: date) -> TimeRange | None:
        return lexical_recovery.resolve_month_period_with_year_hint(period, today=today)

    def _normalize_month_name_without_year(self, extraction: QueryExtractionResult) -> QueryExtractionResult:
        return lexical_recovery.normalize_month_name_without_year(extraction)

    @staticmethod
    def _parse_amount_token(amount_text: str, suffix: str) -> float | None:
        return lexical_recovery.parse_amount_token(amount_text, suffix)

    @classmethod
    def _extract_beneficiary_query_amount_bounds(cls, raw_query: str) -> tuple[float | None, float | None] | None:
        return lexical_recovery.extract_beneficiary_query_amount_bounds(raw_query)

    @staticmethod
    def _extract_relative_time_range_from_query(raw_query: str) -> QueryTimeRange | None:
        return lexical_recovery.extract_relative_time_range_from_query(raw_query)

    def _recover_known_fragile_query_shapes(
        self,
        extraction: QueryExtractionResult,
        *,
        language: str = "en",
    ) -> QueryExtractionResult:
        return lexical_recovery.recover_known_fragile_query_shapes(extraction, language=language)

    @staticmethod
    def parse_clarification_time_range(
        message: str,
        *,
        today: date,
    ) -> QueryTimeRange | None:
        return lexical_recovery.parse_clarification_time_range(message, today=today)

    @staticmethod
    def _render_resolver_prompt_message(prompt: Prompt, language: str) -> str:
        return finalize_compiler.render_resolver_prompt_message(prompt, language)

    def parse_deterministic(
        self,
        question: str,
        *,
        today: date,
        language: str = "en",
    ) -> "QueryParseResult | None":
        return finalize_compiler.parse_deterministic(self, question, today=today, language=language)

    async def parse(
        self,
        question: str,
        today: date,
        language: str = "en",
    ) -> "QueryParseResult":
        return await finalize_compiler.parse(self, question, today=today, language=language)

    def resolve_existing_extraction(
        self,
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        return finalize_compiler.resolve_existing_extraction(self, extraction, today=today, language=language)

    def compile_extraction(
        self,
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        return finalize_compiler.compile_extraction(self, extraction, today=today, language=language)

    def compile_reasoner_extraction(
        self,
        extraction: QueryExtractionResult | ReasonerQueryExtraction,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        return finalize_compiler.compile_reasoner_extraction(self, extraction, today=today, language=language)

    def _validate_capabilities(self, extraction: "QueryExtractionResult") -> None:
        finalize_compiler.validate_capabilities(extraction)

    def _requires_time_comparison_period(self, extraction: "QueryExtractionResult") -> bool:
        return finalize_compiler.requires_time_comparison_period(extraction)

    def build_query_ir_from_extraction(
        self,
        extraction: "QueryExtractionResult",
        *,
        today: date | None = None,
        language: str = "en",
        continuation_type: str | None = None,
        continuation_delta_type: str | None = None,
    ) -> QueryIR:
        return query_compiler.build_query_ir_from_extraction(
            self,
            extraction,
            today=today,
            language=language,
            continuation_type=continuation_type,
            continuation_delta_type=continuation_delta_type,
        )

    @staticmethod
    def _resolve_period_to_range(
        period: str,
        *,
        today: date,
        current_range: TimeRange | None = None,
    ) -> TimeRange | None:
        return query_compiler.resolve_period_to_range(period, today=today, current_range=current_range)

    def _build_comparison_directive(
        self,
        extraction: "QueryExtractionResult",
        *,
        intent: QueryIntent,
        current_range: TimeRange,
        today: date,
    ) -> ComparisonDirective | None:
        return query_compiler.build_comparison_directive(
            self,
            extraction,
            intent=intent,
            current_range=current_range,
            today=today,
        )

    def build_execution_contract_from_ir(self, query_ir: QueryIR) -> QueryExecutionContract:
        return query_compiler.build_execution_contract_from_ir(query_ir)

    def _compile_query_fields_from_extraction(
        self,
        extraction: "QueryExtractionResult",
        *,
        today: date,
        language: str = "en",
    ) -> dict[str, Any]:
        return query_compiler.compile_query_fields_from_extraction(self, extraction, today=today, language=language)

    def _resolve_effective_intent(self, extraction: "QueryExtractionResult") -> ExtractionIntent:
        effective_intent = query_compiler.resolve_effective_intent(
            extraction.raw_query,
            extraction.intent,
            request_shape=extraction.request_shape,
            fact_query_kind=extraction.fact_query_kind,
            answer_fact_field=extraction.answer_fact_field,
        )
        if effective_intent != extraction.intent:
            logger.info(
                "query_parser_intent_recovered_from_list_misclassification",
                original_intent=extraction.intent.value,
                recovered_intent=effective_intent.value,
            )
        return effective_intent

    @staticmethod
    def _intent_from_query_operation(query_operation: QueryOperation) -> QueryIntent:
        return query_compiler.intent_from_query_operation(query_operation)

    def _infer_query_operation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
    ) -> QueryOperation:
        return query_compiler.infer_query_operation(extraction, effective_intent=effective_intent)

    @staticmethod
    def _is_aggregate_total_query(raw_query: str) -> bool:
        return query_compiler.is_aggregate_total_query(raw_query)

    @staticmethod
    def _resolve_result_limit(raw_limit: int | None, *, effective_intent: ExtractionIntent) -> int | None:
        return query_compiler.resolve_result_limit(raw_limit, effective_intent=effective_intent)

    @staticmethod
    def _build_time_range(
        extraction: "QueryExtractionResult",
        *,
        today: date,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
        answer_fact_field: Literal["date", "counterparty", "amount", "bank"] | None,
        result_reference: Literal["latest", "oldest"] | None,
    ) -> TimeRange | None:
        return query_compiler.build_time_range(
            extraction,
            today=today,
            effective_intent=effective_intent,
            query_operation=query_operation,
            answer_fact_field=answer_fact_field,
            result_reference=result_reference,
        )

    @staticmethod
    def _infer_result_reference(
        extraction: "QueryExtractionResult",
        *,
        query_operation: QueryOperation,
    ) -> Literal["latest", "oldest"] | None:
        return query_compiler.infer_result_reference(extraction, query_operation=query_operation)

    @staticmethod
    def _infer_transaction_type(
        *,
        extracted_transaction_type: str | None,
        raw_query: str | None,
        effective_intent: ExtractionIntent,
    ) -> Literal["credit", "debit"] | None:
        return query_compiler.infer_transaction_type(
            extracted_transaction_type=extracted_transaction_type,
            raw_query=raw_query,
            effective_intent=effective_intent,
        )

    def _build_filters(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Filters | None:
        return query_compiler.build_filters(
            self,
            extraction,
            effective_intent=effective_intent,
            query_operation=query_operation,
        )

    @classmethod
    def _normalize_counterparty_filter(cls, recipient: str | None) -> str | None:
        return query_compiler.normalize_counterparty_filter(cls._COUNTERPARTY_PLACEHOLDERS, recipient)

    @staticmethod
    def _infer_answer_fact_field(
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Literal["date", "counterparty", "amount", "bank"] | None:
        return query_compiler.infer_answer_fact_field(
            extraction,
            effective_intent=effective_intent,
            query_operation=query_operation,
        )

    @staticmethod
    def _coerce_aggregation_type(agg_type: str) -> Literal["sum", "average", "count", "largest", "smallest", "breakdown"]:
        return query_compiler.coerce_aggregation_type(agg_type)

    @staticmethod
    def _coerce_group_by(group_by: str | None) -> Literal["category", "merchant", "day", "account", "transaction_type"] | None:
        return query_compiler.coerce_group_by(group_by)

    def _infer_breakdown_group_by(
        self, extraction: "QueryExtractionResult"
    ) -> Literal["category", "merchant", "day", "account", "transaction_type"] | None:
        return query_compiler.infer_breakdown_group_by(extraction)

    @staticmethod
    def _coerce_sort_by(sort_by: str | None) -> Literal["amount", "count"] | None:
        return query_compiler.coerce_sort_by(sort_by)

    def _build_aggregation_from_extracted(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
    ) -> Aggregation | None:
        return query_compiler.build_aggregation_from_extracted(extraction, effective_intent=effective_intent)

    def _build_default_aggregation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Aggregation | None:
        return query_compiler.build_default_aggregation(
            self,
            extraction,
            effective_intent=effective_intent,
            query_operation=query_operation,
        )

    def _build_aggregation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Aggregation | None:
        return query_compiler.build_aggregation(
            self,
            extraction,
            effective_intent=effective_intent,
            query_operation=query_operation,
        )

    @staticmethod
    def _normalize_operation_aggregation(
        aggregation: Aggregation | None,
        *,
        query_operation: QueryOperation,
    ) -> Aggregation | None:
        return query_compiler.normalize_operation_aggregation(aggregation, query_operation=query_operation)

    @staticmethod
    def _normalize_extrema_aggregation(aggregation: Aggregation | None, *, raw_query: str | None) -> Aggregation | None:
        return query_compiler.normalize_extrema_aggregation(aggregation, raw_query=raw_query)
