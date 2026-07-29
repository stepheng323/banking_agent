"""Query parsing service - extracts typed QueryRequest operations from natural language."""

from datetime import date
from typing import Any

from langchain_core.runnables import Runnable

from banking.transactions.query.compiler import finalize as finalize_compiler
from banking.transactions.query.compiler import lexical_recovery, query_compiler
from banking.transactions.query.compiler.resolver import Prompt
from banking.transactions.query.compiler.v2 import compile_query_request
from banking.transactions.query.models.domain import QueryIntent, TimeRange
from banking.transactions.query.models.extraction import (
    FactQueryKind,
    ParserQueryExtraction,
    PendingClarificationState,
    QueryExtractionResult,
    QueryParseResult,
    QueryRequestShape,
    QueryTimeRange,
    ReasonerQueryExtraction,
)
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
    """Parse natural language financial questions into QueryRequest."""

    def __init__(self, llm: Runnable):
        self.llm = llm

    def _build_pending_clarification(
        self,
        *,
        extraction: QueryExtractionResult,
        language: str,
        message: str | None,
        resolver_message: str | None,
    ) -> PendingClarificationState:
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
        return finalize_compiler.derive_ambiguities(extraction)

    def _derive_requested_capabilities(
        self,
        extraction: QueryExtractionResult,
        *,
        intent: QueryIntent,
    ):
        return finalize_compiler.derive_requested_capabilities(
            self,
            extraction,
            intent=intent,
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

    @staticmethod
    def looks_like_support_problem_statement(raw_query: str | None) -> bool:
        return lexical_recovery.looks_like_support_problem_statement(raw_query)

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

    def build_query_request_from_extraction(
        self,
        extraction: "QueryExtractionResult",
        *,
        today: date,
        language: str = "en",
    ):
        """Compile extraction directly into Query Semantics v2."""
        return compile_query_request(self, extraction, today=today, language=language)

    def _compile_query_fields_from_extraction(
        self,
        extraction: "QueryExtractionResult",
        *,
        today: date,
        language: str = "en",
    ) -> dict[str, Any]:
        return query_compiler.compile_query_fields_from_extraction(self, extraction, today=today, language=language)
