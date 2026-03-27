"""Query parsing service - extracts QueryIR/QueryExecutionContract from natural language."""

import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Literal, cast

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.capabilities import (
    QUERY_LIMITS,
)
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    Ambiguity,
    ComparisonDirective,
    ExtractionIntent,
    Filters,
    NormalizedQuery,
    ParserQueryExtraction,
    PendingClarificationState,
    QueryAggregation,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryIR,
    QueryOperation,
    QueryParseResult,
    QueryTimeRange,
    ReasonerQueryExtraction,
    RequestedCapability,
    ResolverOutcome,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.models.extraction import AmbiguityCode
from apps.core.src.agent.graphs.query.prompts import QUERY_PARSER_PROMPT
from apps.core.src.agent.graphs.query.services.resolver import Decision, Prompt, resolve
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from shared.i18n import render_message
from shared.i18n.message_keys import MessageKey
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
    ) -> PendingClarificationState:
        return PendingClarificationState(
            original_query=message or extraction.raw_query or "",
            current_intent=self._resolve_effective_intent(extraction),
            original_extraction=extraction.model_copy(deep=True),
            ambiguities=list(extraction.ambiguities),
            resolver_message=resolver_message,
            language=language,
        )

    def _finalize_extraction(
        self,
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        extraction = extraction.model_copy(deep=True)
        extraction = self._normalize_month_name_without_year(extraction)

        # Deterministic capability validation
        self._validate_capabilities(extraction)

        # Run through resolver
        decision = resolve(extraction, language=language)

        outcome = ResolverOutcome.OK
        message = None
        notices = []
        pending_clarification: PendingClarificationState | None = None

        if decision.decision == Decision.ASK_CLARIFY:
            outcome = ResolverOutcome.NEEDS_INPUT
            message = (
                self._render_resolver_prompt_message(decision.prompts[0], language)
                if decision.prompts
                else render_message("query.clarify.default", language)
            )
            pending_clarification = self._build_pending_clarification(
                extraction=decision.extraction,
                language=language,
                message=extraction.raw_query,
                resolver_message=message,
            )

        elif decision.decision == Decision.NEGOTIATE:
            outcome = ResolverOutcome.NEGOTIATED
            if decision.negotiation:
                message = decision.negotiation.message

        if decision.clamped.days_back:
            notices.append(
                render_message(
                    "query.notice.clamped_days",
                    language,
                    {"days_back": decision.clamped.days_back},
                )
            )

        if self._requires_time_comparison_period(decision.extraction):
            clarify_message = render_message("query.time_comparison.prompt_specify_period", language)
            pending_clarification = self._build_pending_clarification(
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

        query_ir = self.build_query_ir_from_extraction(
            decision.extraction,
            today=today,
            language=language,
        )
        query_contract = self.build_execution_contract_from_ir(query_ir)

        return QueryParseResult(
            outcome=outcome,
            extraction=decision.extraction,
            query_ir=query_ir.model_dump(),
            query_contract=query_contract.model_dump(),
            resolver_message=message,
            notices=notices,
            pending_clarification=pending_clarification.model_dump() if pending_clarification else None,
            patch={},
        )

    @staticmethod
    def _looks_like_vague_time_phrase(raw_query: str) -> str | None:
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

    def _derive_ambiguities(
        self,
        extraction: QueryExtractionResult,
    ) -> list[Ambiguity]:

        ambiguities: list[Ambiguity] = []
        if extraction.time_range.reference_type == TimeReference.VAGUE:
            context = extraction.time_range.period or self._looks_like_vague_time_phrase(extraction.raw_query or "") or "that time"
            ambiguities.append(Ambiguity(code=AmbiguityCode.TIME_VAGUE, context=context))
        return ambiguities

    def _derive_requested_capabilities(
        self,
        extraction: QueryExtractionResult,
        *,
        effective_intent: ExtractionIntent,
    ) -> list[RequestedCapability]:

        requested_capabilities: list[RequestedCapability] = []

        def _add(capability: RequestedCapability) -> None:
            if capability not in requested_capabilities:
                requested_capabilities.append(capability)

        query_operation = self._infer_query_operation(extraction, effective_intent=effective_intent)
        inferred_transaction_type = self._infer_transaction_type(
            extracted_transaction_type=extraction.filters.transaction_type,
            raw_query=extraction.raw_query,
            effective_intent=effective_intent,
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

        if effective_intent == ExtractionIntent.TIME_COMPARISON or query_operation == QueryOperation.COMPARE_PERIODS:
            _add(RequestedCapability.TIME_COMPARISON)

        if query_operation in {
            QueryOperation.SUM_TRANSACTIONS,
            QueryOperation.COUNT_TRANSACTIONS,
            QueryOperation.AVERAGE_TRANSACTIONS,
            QueryOperation.RANK_LARGEST_TRANSACTION,
            QueryOperation.RANK_SMALLEST_TRANSACTION,
        }:
            _add(RequestedCapability.AGGREGATE_SUM)

        if query_operation == QueryOperation.BREAKDOWN_TRANSACTIONS or effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY:
            _add(RequestedCapability.AGGREGATE_GROUP)

        return requested_capabilities

    def _inflate_parser_extraction(
        self,
        extraction: QueryExtractionResult | ParserQueryExtraction,
        *,
        question: str,
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
                result_limit=extraction.result_limit,
                result_reference=extraction.result_reference,
                answer_fact_field=extraction.answer_fact_field,
            )

        inflated.raw_query = question
        effective_intent = self._resolve_effective_intent(inflated)
        inflated.query_operation = self._infer_query_operation(inflated, effective_intent=effective_intent)
        if not inflated.requested_capabilities:
            inflated.requested_capabilities = self._derive_requested_capabilities(
                inflated,
                effective_intent=effective_intent,
            )
        if not inflated.ambiguities:
            inflated.ambiguities = self._derive_ambiguities(inflated)
        return inflated

    @staticmethod
    def _month_token(period: str | None) -> int | None:
        if not period:
            return None
        token = period.strip().lower().replace("-", "_").replace(" ", "_")
        return _MONTH_NAME_TO_NUMBER.get(token)

    @staticmethod
    def _resolve_month_period_with_year_hint(period: str, *, today: date) -> TimeRange | None:
        normalized = period.strip().lower().replace("-", "_").replace(" ", "_")
        parts = [part for part in normalized.split("_") if part]
        if not parts:
            return None

        month_number: int | None = None
        for part in parts:
            month_number = _MONTH_NAME_TO_NUMBER.get(part)
            if month_number is not None:
                break
        if month_number is None:
            return None

        if any(token in normalized for token in _THIS_YEAR_TOKENS):
            year = today.year
        elif any(token in normalized for token in _LAST_YEAR_TOKENS):
            year = today.year - 1
        else:
            year = today.year if month_number <= today.month else today.year - 1

        month_start = date(year, month_number, 1)
        month_end = date(year, month_number, monthrange(year, month_number)[1])
        if year == today.year and month_number == today.month:
            month_end = today
        return TimeRange(start=month_start, end=month_end, granularity="month")

    def _normalize_month_name_without_year(self, extraction: QueryExtractionResult) -> QueryExtractionResult:
        month_number = self._month_token(extraction.time_range.period)
        if month_number is None:
            return extraction

        extraction.time_range.reference_type = TimeReference.EXPLICIT
        extraction.time_range.days_back = None
        month_names = {
            period
            for period, number in _MONTH_NAME_TO_NUMBER.items()
            if number == month_number
        }
        extraction.ambiguities = [
            ambiguity
            for ambiguity in extraction.ambiguities
            if not (
                ambiguity.code == AmbiguityCode.TIME_VAGUE
                and (
                    "no year specified" in (ambiguity.context or "").lower()
                    or any(name in (ambiguity.context or "").lower() for name in month_names)
                )
            )
        ]
        return extraction

    @staticmethod
    def parse_clarification_time_range(
        message: str,
        *,
        today: date,
    ) -> QueryTimeRange | None:
        normalized = " ".join(message.lower().strip().split()).rstrip("?.!,")
        mapping = {
            "today": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
            "yesterday": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday", days_back=1),
            "this week": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
            "last week": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
            "this month": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
            "last month": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_month"),
            "this year": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_year"),
            "last year": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_year"),
            "all time": QueryTimeRange(reference_type=TimeReference.ALL_TIME),
            "ever": QueryTimeRange(reference_type=TimeReference.ALL_TIME),
        }
        if normalized in mapping:
            return mapping[normalized]

        if QueryParser._month_token(normalized) is not None or QueryParser._resolve_month_period_with_year_hint(
            normalized,
            today=today,
        ) is not None:
            return QueryTimeRange(reference_type=TimeReference.EXPLICIT, period=normalized.replace(" ", "_"))

        match = re.fullmatch(r"(last|past)\s+(\d{1,3})\s+(day|days|week|weeks|month|months)", normalized)
        if not match:
            return None

        amount = max(1, int(match.group(2)))
        unit = match.group(3)
        if unit.startswith("week"):
            amount *= 7
        elif unit.startswith("month"):
            amount *= 30
        return QueryTimeRange(reference_type=TimeReference.EXPLICIT, days_back=amount)

    @staticmethod
    def _render_resolver_prompt_message(prompt: Prompt, language: str) -> str:
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

    def parse_deterministic(
        self,
        question: str,
        *,
        today: date,
        language: str = "en",
    ) -> "QueryParseResult | None":
        """
        Fast-path extraction for very common query shapes to avoid LLM latency.
        Returns a QueryParseResult if a match is found, else None.
        """
        normalized = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.!,")
        if not normalized:
            return None

        if re.fullmatch(r"(?:(?:show|list|view|get)\s+)?(?:my\s+)?(?:transactions?|transaction\s+history|history|statement)", normalized):
            extraction = QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                query_operation=QueryOperation.LIST_TRANSACTIONS,
                raw_query=question,
                time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
            )
            return self._finalize_extraction(extraction, today=today, language=language)

        match = re.fullmatch(r"(?:(?:show|list|view|get)\s+)?(?:my\s+)?last\s+(\d+)\s+transactions?", normalized)
        if match:
            limit = int(match.group(1))
            extraction = QueryExtractionResult(
                intent=ExtractionIntent.TRANSACTION_LIST,
                query_operation=QueryOperation.LIST_TRANSACTIONS,
                raw_query=question,
                time_range=QueryTimeRange(reference_type=TimeReference.UNSPECIFIED),
                result_limit=limit,
                result_reference="latest",
            )
            return self._finalize_extraction(extraction, today=today, language=language)

        match = re.fullmatch(r"how\s+much\s+(?:did|have)\s+i\s+(spend|spent|send|sent|pay|paid|receive|received)\s+(today|this week|this month|last week|last month|yesterday)", normalized)
        if match:
            action, period_phrase = match.groups()
            tx_type = "credit" if action in ("receive", "received") else "debit"

            period_map = {
                "today": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="today", days_back=0),
                "yesterday": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="yesterday", days_back=1),
                "this week": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_week"),
                "this month": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
                "last week": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_week"),
                "last month": QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="last_month"),
            }
            time_range = period_map[period_phrase]
            extraction = QueryExtractionResult(
                intent=ExtractionIntent.SPENDING_TOTAL,
                query_operation=QueryOperation.SUM_TRANSACTIONS,
                raw_query=question,
                time_range=time_range,
                filters=QueryFilters(transaction_type=tx_type),
                aggregation=QueryAggregation(type="sum"),
            )
            return self._finalize_extraction(extraction, today=today, language=language)

        return None

    async def parse(
        self,
        question: str,
        today: date,
        language: str = "en",
    ) -> "QueryParseResult":
        """
        Fallback LLM extraction path for queries whose meaning is not already usable.

        Args:
            question: User's natural language query
            today: Today's date (context-aware)

        Returns:
            QueryParseResult with outcome and extraction details
        """
        prompt = QUERY_PARSER_PROMPT.format(
            today=today.isoformat(),
            question=question,
        )

        structured_llm = cast(Any, self.llm).with_structured_output(ParserQueryExtraction)

        try:
            raw_extraction = await structured_llm.ainvoke(prompt)
            extraction = self._inflate_parser_extraction(raw_extraction, question=question)
            return self._finalize_extraction(extraction, today=today, language=language)

        except Exception as e:
            logger.error("parse_error", error=str(e))

        return QueryParseResult(
            outcome=ResolverOutcome.OK,  # Fallback to try best effort
            extraction=QueryExtractionResult(raw_query=question),
        )

    def resolve_existing_extraction(
        self,
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        """Resolve a pre-extracted query after semantic clarification patching."""
        return self._finalize_extraction(extraction, today=today, language=language)

    def compile_extraction(
        self,
        extraction: QueryExtractionResult,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        """Compile already-interpreted query meaning through deterministic validation and resolver stages."""
        return self.resolve_existing_extraction(extraction, today=today, language=language)

    def compile_reasoner_extraction(
        self,
        extraction: QueryExtractionResult | ReasonerQueryExtraction,
        *,
        today: date,
        language: str,
    ) -> QueryParseResult:
        """Compile a minimal reasoner extraction without requiring a second LLM call."""
        full_extraction = (
            extraction.to_query_extraction_result()
            if isinstance(extraction, ReasonerQueryExtraction)
            else extraction.model_copy(deep=True)
        )
        return self.compile_extraction(full_extraction, today=today, language=language)

    def _validate_capabilities(self, extraction: "QueryExtractionResult") -> None:
        """Enforce capability dependencies deterministically."""
        from apps.core.src.agent.graphs.query.models import (
            RequestedCapability,
            TimeReference,
        )

        if not extraction.filters.narration_keyword:
            extraction.requested_capabilities = [
                capability
                for capability in extraction.requested_capabilities
                if capability
                not in {
                    RequestedCapability.SEARCH_NARRATION_KEYWORD,
                    RequestedCapability.SEARCH_NARRATION_FUZZY,
                }
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
        else:
            if RequestedCapability.TIME_ALL in extraction.requested_capabilities:
                extraction.requested_capabilities.remove(RequestedCapability.TIME_ALL)

    def _requires_time_comparison_period(self, extraction: "QueryExtractionResult") -> bool:
        """Ensure time-comparison requests include an explicit comparison window."""
        if extraction.intent != ExtractionIntent.TIME_COMPARISON:
            return False
        return extraction.time_range.reference_type == TimeReference.UNSPECIFIED

    def build_query_ir_from_extraction(
        self,
        extraction: "QueryExtractionResult",
        *,
        today: date | None = None,
        language: str = "en",
        continuation_type: str | None = None,
        continuation_delta_type: str | None = None,
    ) -> QueryIR:
        """Compile LLM extraction into the intermediate query representation."""
        normalized = self.convert_to_normalized(extraction, today=today)
        base_today = today or lagos_today()
        if normalized.time_range is None:
            normalized.time_range = TimeRange(start=base_today - timedelta(days=30), end=base_today, granularity="day")

        comparison = self._build_comparison_directive(
            extraction,
            intent=normalized.intent,
            current_range=normalized.time_range,
            today=base_today,
        )

        return QueryIR(
            intent=normalized.intent,
            query_operation=normalized.query_operation,
            raw_query=extraction.raw_query,
            language=language,
            timezone="Africa/Lagos",
            time_range=normalized.time_range,
            filters=normalized.filters,
            aggregation=normalized.aggregation,
            accounts_scope=normalized.accounts_scope,
            account_name=normalized.account_name,
            amount_check=normalized.amount_check,
            item_name=normalized.item_name,
            analysis_type=normalized.analysis_type,
            result_limit=normalized.result_limit,
            result_reference=normalized.result_reference,
            answer_fact_field=normalized.answer_fact_field,
            comparison=comparison,
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
        def _duration_days(range_value: TimeRange | None) -> int:
            if range_value is None:
                return 0
            return max(1, (range_value.end - range_value.start).days + 1)

        token = period.strip().lower().replace("-", "_").replace(" ", "_")
        if token in {"today"}:
            return TimeRange(start=today, end=today, granularity="day")
        if token in {"yesterday"}:
            day = today - timedelta(days=1)
            return TimeRange(start=day, end=day, granularity="day")
        if token in {"this_week", "week", "current_week"}:
            week_start = today - timedelta(days=today.weekday())
            return TimeRange(start=week_start, end=today, granularity="week")
        if token in {"last_week", "previous_week"}:
            this_week_start = today - timedelta(days=today.weekday())
            week_end = this_week_start - timedelta(days=1)
            week_start = week_end - timedelta(days=6)
            duration = _duration_days(current_range)
            if duration <= 0:
                return TimeRange(start=week_start, end=week_end, granularity="week")
            aligned_end = min(week_end, week_start + timedelta(days=duration - 1))
            return TimeRange(start=week_start, end=aligned_end, granularity="week")
        if token in {"this_month", "current_month", "month"}:
            month_start = date(today.year, today.month, 1)
            return TimeRange(start=month_start, end=today, granularity="month")
        if token in {"last_month", "previous_month"}:
            year = today.year
            month = today.month - 1
            if month == 0:
                month = 12
                year -= 1
            last_day = monthrange(year, month)[1]
            month_start = date(year, month, 1)
            month_end = date(year, month, last_day)
            duration = _duration_days(current_range)
            if duration <= 0:
                return TimeRange(start=month_start, end=month_end, granularity="month")
            aligned_end = min(month_end, month_start + timedelta(days=duration - 1))
            return TimeRange(start=month_start, end=aligned_end, granularity="month")
        if token in {"this_year", "current_year", "year"}:
            return TimeRange(start=date(today.year, 1, 1), end=today, granularity="month")
        if token in {"last_year", "previous_year"}:
            year = today.year - 1
            return TimeRange(start=date(year, 1, 1), end=date(year, 12, 31), granularity="month")
        hinted_month_range = QueryParser._resolve_month_period_with_year_hint(period, today=today)
        if hinted_month_range is not None:
            return hinted_month_range
        month_number = QueryParser._month_token(token)
        if month_number is not None:
            year = today.year if month_number <= today.month else today.year - 1
            month_start = date(year, month_number, 1)
            month_end = date(year, month_number, monthrange(year, month_number)[1])
            if year == today.year and month_number == today.month:
                month_end = today
            return TimeRange(start=month_start, end=month_end, granularity="month")
        return None

    def _build_comparison_directive(
        self,
        extraction: "QueryExtractionResult",
        *,
        intent: QueryIntent,
        current_range: TimeRange,
        today: date,
    ) -> ComparisonDirective | None:
        if intent != QueryIntent.TIME_COMPARISON:
            return None

        comparison = extraction.comparison
        if comparison is None:
            return ComparisonDirective(mode="previous_equivalent")

        if comparison.mode == "year_ago":
            return ComparisonDirective(mode="year_ago")

        if comparison.mode == "explicit_period" and comparison.period:
            explicit_range = self._resolve_period_to_range(
                comparison.period,
                today=today,
                current_range=current_range,
            )
            if explicit_range is not None:
                return ComparisonDirective(mode="explicit_range", explicit_range=explicit_range)
            # Invalid explicit period falls back deterministically.
            return ComparisonDirective(mode="previous_equivalent")

        # Keep a deterministic default for underspecified comparison directives.
        return ComparisonDirective(mode="previous_equivalent")

    def build_execution_contract_from_ir(self, query_ir: QueryIR) -> QueryExecutionContract:
        """Compile runtime contract from QueryIR."""
        return QueryExecutionContract.from_query_ir(query_ir)

    def _resolve_effective_intent(self, extraction: "QueryExtractionResult") -> ExtractionIntent:
        raw_lower = (extraction.raw_query or "").strip().lower()
        if extraction.intent == ExtractionIntent.TRANSACTION_LIST and self._is_aggregate_total_query(raw_lower):
            logger.info(
                "query_parser_intent_recovered_from_list_misclassification",
                original_intent=extraction.intent.value,
                recovered_intent=ExtractionIntent.SPENDING_TOTAL.value,
            )
            return ExtractionIntent.SPENDING_TOTAL
        return extraction.intent

    @staticmethod
    def _intent_from_query_operation(query_operation: QueryOperation) -> QueryIntent:
        if query_operation == QueryOperation.LIST_TRANSACTIONS:
            return QueryIntent.TRANSACTION_LIST
        if query_operation == QueryOperation.SEARCH_SINGLE_TRANSACTION:
            return QueryIntent.TRANSACTION_SEARCH
        if query_operation in {
            QueryOperation.SUM_TRANSACTIONS,
            QueryOperation.COUNT_TRANSACTIONS,
            QueryOperation.AVERAGE_TRANSACTIONS,
            QueryOperation.RANK_LARGEST_TRANSACTION,
            QueryOperation.RANK_SMALLEST_TRANSACTION,
            QueryOperation.BREAKDOWN_TRANSACTIONS,
        }:
            return QueryIntent.ANALYTICS_SUMMARY
        if query_operation == QueryOperation.COMPARE_PERIODS:
            return QueryIntent.TIME_COMPARISON
        if query_operation == QueryOperation.SUMMARIZE_BENEFICIARIES:
            return QueryIntent.BENEFICIARY_SUMMARY
        return QueryIntent.AFFORDABILITY

    def _infer_query_operation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
    ) -> QueryOperation:
        if extraction.query_operation is not None:
            return extraction.query_operation

        aggregation_type = extraction.aggregation.type if extraction.aggregation is not None else None
        if aggregation_type == "count":
            return QueryOperation.COUNT_TRANSACTIONS
        if aggregation_type == "average":
            return QueryOperation.AVERAGE_TRANSACTIONS
        if aggregation_type == "largest":
            return QueryOperation.RANK_LARGEST_TRANSACTION
        if aggregation_type == "smallest":
            return QueryOperation.RANK_SMALLEST_TRANSACTION
        if aggregation_type == "breakdown":
            return QueryOperation.BREAKDOWN_TRANSACTIONS

        if effective_intent == ExtractionIntent.SINGLE_TRANSACTION:
            return QueryOperation.SEARCH_SINGLE_TRANSACTION
        if effective_intent == ExtractionIntent.SPENDING_TOTAL:
            return QueryOperation.SUM_TRANSACTIONS
        if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN:
            return QueryOperation.BREAKDOWN_TRANSACTIONS
        if effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY:
            return QueryOperation.SUMMARIZE_BENEFICIARIES
        if effective_intent == ExtractionIntent.TIME_COMPARISON:
            return QueryOperation.COMPARE_PERIODS
        if effective_intent == ExtractionIntent.AFFORDABILITY:
            return QueryOperation.CHECK_AFFORDABILITY
        return QueryOperation.LIST_TRANSACTIONS

    @staticmethod
    def _is_aggregate_total_query(raw_query: str) -> bool:
        if not raw_query:
            return False
        if not any(cue in raw_query for cue in ("how much", "total", "sum")):
            return False
        return any(
            cue in raw_query
            for cue in (
                "spend",
                "spent",
                "spending",
                "expense",
                "expenses",
                "pay",
                "paid",
                "send",
                "sent",
                "transfer",
                "transferred",
                "receive",
                "received",
                "credit",
                "credited",
                "income",
                "inflow",
            )
        )

    @staticmethod
    def _resolve_result_limit(raw_limit: int | None, *, effective_intent: ExtractionIntent) -> int | None:
        result_limit = raw_limit
        if effective_intent == ExtractionIntent.SINGLE_TRANSACTION:
            result_limit = result_limit or 1

        if result_limit:
            result_limit = min(result_limit, QUERY_LIMITS["max_results"])

        return result_limit

    @staticmethod
    def _build_time_range(extraction: "QueryExtractionResult", *, today: date) -> TimeRange | None:
        if not extraction.time_range:
            return None

        days_back = extraction.time_range.days_back
        period_lower = (extraction.time_range.period or "").strip().lower()
        reference_type = extraction.time_range.reference_type

        if reference_type == TimeReference.EXPLICIT and period_lower:
            explicit_range = QueryParser._resolve_period_to_range(period_lower, today=today)
            if explicit_range is not None:
                return explicit_range

        if period_lower == "today":
            days_back = 0
        elif period_lower == "yesterday":
            days_back = 1
        if days_back is None:
            days_back = 30
        if reference_type == TimeReference.ALL_TIME:
            days_back = QUERY_LIMITS["max_lookback_days"]
        elif reference_type == TimeReference.UNSPECIFIED:
            days_back = 30

        range_start = today - timedelta(days=days_back)
        range_end = today
        if period_lower == "today":
            range_start = today
            range_end = today
        elif period_lower == "yesterday":
            yesterday = today - timedelta(days=1)
            range_start = yesterday
            range_end = yesterday

        return TimeRange(start=range_start, end=range_end, granularity="day")

    @staticmethod
    def _infer_transaction_type(
        *,
        extracted_transaction_type: str | None,
        raw_query: str | None,
        effective_intent: ExtractionIntent,
    ) -> Literal["credit", "debit"] | None:
        transaction_type = (extracted_transaction_type or "").strip().lower() or None
        if not transaction_type:
            raw_lower = (raw_query or "").lower()
            has_explicit_credit_intent = any(
                k in raw_lower for k in ("received", "credited", "income", "salary", "sent me", "from ")
            )
            if has_explicit_credit_intent:
                transaction_type = "credit"

            is_expense_query = not has_explicit_credit_intent and effective_intent in (
                ExtractionIntent.SPENDING_TOTAL,
                ExtractionIntent.CATEGORY_BREAKDOWN,
                ExtractionIntent.BENEFICIARY_SUMMARY,
            )

            if not has_explicit_credit_intent and not is_expense_query and raw_lower:
                if any(k in raw_lower for k in ("spending", "expense", "spent", "cost", "paid", "send", "sent", "transfer", "transferred")):
                    is_expense_query = True

            if is_expense_query:
                transaction_type = "debit"

        if transaction_type in {"credit", "debit"}:
            return cast(Literal["credit", "debit"], transaction_type)
        return None

    def _build_filters(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Filters | None:
        if not extraction.filters:
            return None

        group_by = (extraction.aggregation.group_by or "").strip().lower() if extraction.aggregation else ""
        counterparty = self._normalize_counterparty_filter(extraction.filters.recipient)
        transaction_type = None
        if group_by not in {"transaction_type", "type"}:
            transaction_type = self._infer_transaction_type(
                extracted_transaction_type=extraction.filters.transaction_type,
                raw_query=extraction.raw_query,
                effective_intent=effective_intent,
            )

        return Filters(
            merchant=[extraction.filters.narration_keyword] if extraction.filters.narration_keyword else None,
            counterparty=[counterparty] if counterparty else None,
            category=[extraction.filters.category] if extraction.filters.category else None,
            min_amount=extraction.filters.min_amount,
            max_amount=extraction.filters.max_amount,
            transaction_type=transaction_type,
            account_filter=extraction.filters.bank,
        )

    @classmethod
    def _normalize_counterparty_filter(cls, recipient: str | None) -> str | None:
        normalized = " ".join((recipient or "").strip().split())
        if not normalized:
            return None

        lowered = normalized.casefold()
        if lowered in cls._COUNTERPARTY_PLACEHOLDERS or lowered.startswith("unknown "):
            return None

        return normalized

    @staticmethod
    def _infer_answer_fact_field(
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Literal["date", "counterparty", "amount", "bank"] | None:
        if extraction.answer_fact_field in {"date", "counterparty", "amount", "bank"}:
            return cast(Literal["date", "counterparty", "amount", "bank"], extraction.answer_fact_field)

        if effective_intent in {
            ExtractionIntent.SPENDING_TOTAL,
            ExtractionIntent.CATEGORY_BREAKDOWN,
            ExtractionIntent.BENEFICIARY_SUMMARY,
            ExtractionIntent.TIME_COMPARISON,
            ExtractionIntent.AFFORDABILITY,
        }:
            return None

        if query_operation not in {QueryOperation.LIST_TRANSACTIONS, QueryOperation.SEARCH_SINGLE_TRANSACTION}:
            return None

        raw_query = f" {(extraction.raw_query or '').strip().lower()} "
        if raw_query == "  ":
            return None
        if raw_query.startswith(" when ") or " when did " in raw_query:
            return "date"
        if raw_query.startswith(" who ") or " who sent " in raw_query or " who paid " in raw_query:
            return "counterparty"
        if raw_query.startswith(" which bank ") or raw_query.startswith(" what bank "):
            return "bank"
        if raw_query.startswith(" how much was ") or raw_query.startswith(" how much did i pay for "):
            return "amount"
        return None

    @staticmethod
    def _coerce_aggregation_type(agg_type: str) -> Literal["sum", "average", "count", "largest", "smallest", "breakdown"]:
        return cast(
            Literal["sum", "average", "count", "largest", "smallest", "breakdown"],
            agg_type if agg_type in {"sum", "average", "count", "largest", "smallest", "breakdown"} else "sum",
        )

    @staticmethod
    def _coerce_group_by(group_by: str | None) -> Literal["category", "merchant", "day", "account", "transaction_type"] | None:
        normalized = (group_by or "").strip().lower()
        if normalized == "type":
            normalized = "transaction_type"
        if normalized in {"category", "merchant", "day", "account", "transaction_type"}:
            return cast(Literal["category", "merchant", "day", "account", "transaction_type"], normalized)
        return None

    def _infer_breakdown_group_by(
        self, extraction: "QueryExtractionResult"
    ) -> Literal["category", "merchant", "day", "account", "transaction_type"] | None:
        extracted_group_by = self._coerce_group_by(extraction.aggregation.group_by) if extraction.aggregation is not None else None
        if extracted_group_by is not None:
            return extracted_group_by

        raw_lower = f" {(extraction.raw_query or '').strip().lower()} "
        if any(hint in raw_lower for hint in (" by account ", " per account ", " by bank ", " per bank ", " across accounts ")):
            return "account"
        return None

    @staticmethod
    def _coerce_sort_by(sort_by: str | None) -> Literal["amount", "count"] | None:
        if sort_by in {"amount", "count"}:
            return cast(Literal["amount", "count"], sort_by)
        return None

    def _build_aggregation_from_extracted(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
    ) -> Aggregation | None:
        if not extraction.aggregation:
            return None

        agg_type = extraction.aggregation.type or "sum"
        if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN and agg_type == "sum":
            agg_type = "breakdown"

        aggregation = Aggregation(
            type=self._coerce_aggregation_type(agg_type),
            group_by=self._coerce_group_by(extraction.aggregation.group_by),
            limit=extraction.aggregation.limit or 5,
            sort_by=self._coerce_sort_by(extraction.aggregation.sort_by),
        )
        if agg_type == "breakdown" and not aggregation.group_by:
            aggregation.group_by = "category"
        if effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY and aggregation.sort_by is None:
            aggregation.sort_by = "count"
        return aggregation

    def _build_default_aggregation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Aggregation | None:
        raw_lower = (extraction.raw_query or "").strip().lower()
        if query_operation == QueryOperation.RANK_LARGEST_TRANSACTION or any(
            cue in raw_lower for cue in ("largest", "highest", "biggest", "max", "maximum")
        ):
            return Aggregation(type="largest", limit=1)
        if query_operation == QueryOperation.RANK_SMALLEST_TRANSACTION or any(
            cue in raw_lower for cue in ("smallest", "lowest", "least", "minimum", "min")
        ):
            return Aggregation(type="smallest", limit=1)
        if query_operation == QueryOperation.COUNT_TRANSACTIONS:
            return Aggregation(type="count", limit=5)
        if query_operation == QueryOperation.AVERAGE_TRANSACTIONS:
            return Aggregation(type="average", limit=5)
        if query_operation == QueryOperation.BREAKDOWN_TRANSACTIONS:
            group_by = self._infer_breakdown_group_by(extraction)
            return Aggregation(type="breakdown", group_by=group_by or "category", limit=5)

        if effective_intent == ExtractionIntent.SPENDING_TOTAL:
            return Aggregation(type="sum", limit=5)

        if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN:
            return Aggregation(type="breakdown", group_by=self._infer_breakdown_group_by(extraction) or "category")

        if effective_intent == ExtractionIntent.BENEFICIARY_SUMMARY:
            return Aggregation(type="sum", limit=5, sort_by="count")

        return None

    def _build_aggregation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
        query_operation: QueryOperation,
    ) -> Aggregation | None:
        extracted_aggregation = self._build_aggregation_from_extracted(extraction, effective_intent=effective_intent)
        if extracted_aggregation is not None:
            return self._normalize_operation_aggregation(
                self._normalize_extrema_aggregation(extracted_aggregation, raw_query=extraction.raw_query),
                query_operation=query_operation,
            )
        return self._build_default_aggregation(
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
        if aggregation is None:
            return None

        operation_to_type = {
            QueryOperation.SUM_TRANSACTIONS: "sum",
            QueryOperation.COUNT_TRANSACTIONS: "count",
            QueryOperation.AVERAGE_TRANSACTIONS: "average",
            QueryOperation.RANK_LARGEST_TRANSACTION: "largest",
            QueryOperation.RANK_SMALLEST_TRANSACTION: "smallest",
            QueryOperation.BREAKDOWN_TRANSACTIONS: "breakdown",
        }
        target_type = operation_to_type.get(query_operation)
        if target_type is None:
            return aggregation

        aggregation.type = cast(
            Literal["sum", "average", "count", "largest", "smallest", "breakdown"],
            target_type,
        )
        if query_operation == QueryOperation.BREAKDOWN_TRANSACTIONS and aggregation.group_by is None:
            aggregation.group_by = "category"
        if query_operation in {
            QueryOperation.RANK_LARGEST_TRANSACTION,
            QueryOperation.RANK_SMALLEST_TRANSACTION,
        }:
            aggregation.limit = 1
        return aggregation

    @staticmethod
    def _normalize_extrema_aggregation(aggregation: Aggregation | None, *, raw_query: str | None) -> Aggregation | None:
        if aggregation is None:
            return None

        raw_lower = (raw_query or "").strip().lower()
        singular_extrema = (
            ("largest", ("largest", "highest", "biggest", "max", "maximum")),
            ("smallest", ("smallest", "lowest", "least", "minimum", "min")),
        )
        for agg_type, cues in singular_extrema:
            if any(cue in raw_lower for cue in cues):
                aggregation.type = cast(Literal["sum", "average", "count", "largest", "smallest", "breakdown"], agg_type)
                aggregation.limit = 1
                return aggregation
        if aggregation.type in {"largest", "smallest"}:
            aggregation.limit = 1
        return aggregation

    def convert_to_normalized(
        self,
        extraction: "QueryExtractionResult",
        today: date | None = None,
    ) -> NormalizedQuery:
        """Convert QueryExtractionResult to NormalizedQuery for handlers."""

        today = today or lagos_today()
        effective_intent = self._resolve_effective_intent(extraction)
        query_operation = self._infer_query_operation(extraction, effective_intent=effective_intent)
        result_limit = self._resolve_result_limit(extraction.result_limit, effective_intent=effective_intent)

        time_range = self._build_time_range(extraction, today=today)
        filters = self._build_filters(
            extraction,
            effective_intent=effective_intent,
            query_operation=query_operation,
        )
        aggregation = self._build_aggregation(
            extraction,
            effective_intent=effective_intent,
            query_operation=query_operation,
        )
        answer_fact_field = self._infer_answer_fact_field(
            extraction,
            effective_intent=effective_intent,
            query_operation=query_operation,
        )

        result_reference = extraction.result_reference
        if aggregation is not None and aggregation.type in {"largest", "smallest"}:
            result_reference = None

        return NormalizedQuery(
            intent=self._intent_from_query_operation(query_operation),
            query_operation=query_operation,
            time_range=time_range or TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
            filters=filters,
            aggregation=aggregation,
            accounts_scope="all",
            result_limit=result_limit,
            result_reference=result_reference,
            answer_fact_field=answer_fact_field,
        )
