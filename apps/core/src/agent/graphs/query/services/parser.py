"""Query parsing service - extracts QueryIR/QueryExecutionContract from natural language."""

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Literal, cast

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.capabilities import (
    QUERY_LIMITS,
)
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    ComparisonDirective,
    ExtractionIntent,
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryIntent,
    QueryIR,
    QueryParseResult,
    ResolverOutcome,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.prompts import QUERY_PARSER_PROMPT
from apps.core.src.agent.graphs.query.services.resolver import Decision, resolve
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryParser:
    """Parse natural language financial questions into QueryExecutionContract."""

    def __init__(self, llm: Runnable):
        self.llm = llm

    async def parse(
        self,
        question: str,
        today: date,
        language: str = "en",
    ) -> "QueryParseResult":
        """
        Parse query using extraction with resolver integration.

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

        structured_llm = cast(Any, self.llm).with_structured_output(QueryExtractionResult)

        try:
            extraction: QueryExtractionResult = await structured_llm.ainvoke(prompt)
            extraction.raw_query = question

            # Deterministic capability validation
            self._validate_capabilities(extraction)

            # Run through resolver
            decision = resolve(extraction, language=language)

            outcome = ResolverOutcome.OK
            message = None
            notices = []

            if decision.decision == Decision.ASK_CLARIFY:
                outcome = ResolverOutcome.NEEDS_INPUT
                message = (
                    decision.prompts[0].vars.get("context", render_message("query.clarify.default", language))
                    if decision.prompts
                    else render_message("query.clarify.default", language)
                )

            elif decision.decision == Decision.NEGOTIATE:
                outcome = ResolverOutcome.NEGOTIATED
                if decision.negotiation:
                    message = decision.negotiation.message

            # Add notices for clamping/modifications
            if decision.clamped.days_back:
                notices.append(
                    render_message(
                        "query.notice.clamped_days",
                        language,
                        {"days_back": decision.clamped.days_back},
                    )
                )

            if self._requires_time_comparison_period(decision.extraction):
                return QueryParseResult(
                    outcome=ResolverOutcome.NEEDS_INPUT,
                    extraction=decision.extraction,
                    resolver_message=render_message("query.time_comparison.prompt_specify_period", language),
                    notices=notices,
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
                patch={},
            )

        except Exception as e:
            logger.error("parse_error", error=str(e))

            return QueryParseResult(
                outcome=ResolverOutcome.OK,  # Fallback to try best effort
                extraction=QueryExtractionResult(raw_query=question),
            )

    def _validate_capabilities(self, extraction: "QueryExtractionResult") -> None:
        """Enforce capability dependencies deterministically."""
        from apps.core.src.agent.graphs.query.models import (
            RequestedCapability,
            TimeReference,
        )

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

    @staticmethod
    def _has_targeted_aggregate_spend_or_receive_cue(raw_query: str) -> bool:
        query_lower = raw_query.lower()
        has_aggregate_cue = any(term in query_lower for term in ("how much", "total", "sum"))
        if not has_aggregate_cue:
            return False

        spend_cue = any(term in query_lower for term in ("spend", "spent", "spending", "paid", "pay", "expense", "cost"))
        receive_cue = any(
            term in query_lower for term in ("receive", "received", "credited", "credit", "income", "salary", "earned")
        )
        return spend_cue or receive_cue

    @staticmethod
    def _has_targeted_comparison_cue(raw_query: str) -> bool:
        query_lower = raw_query.lower()
        comparison_terms = (" vs ", " versus ", " compared to ", " compare ", " comparison ", " difference ")
        return any(term in f" {query_lower} " for term in comparison_terms)

    def _requires_time_comparison_period(self, extraction: "QueryExtractionResult") -> bool:
        """Ensure time-comparison requests include an explicit comparison window."""
        effective_intent = self._resolve_effective_intent(extraction)
        if effective_intent != ExtractionIntent.TIME_COMPARISON:
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
        raw_query = extraction.raw_query or ""
        if (
            extraction.intent == ExtractionIntent.TRANSACTION_LIST
            and raw_query
            and self._has_targeted_aggregate_spend_or_receive_cue(raw_query)
        ):
            return ExtractionIntent.SPENDING_TOTAL

        if (
            extraction.intent != ExtractionIntent.TIME_COMPARISON
            and raw_query
            and self._has_targeted_comparison_cue(raw_query)
        ):
            return ExtractionIntent.TIME_COMPARISON

        return extraction.intent

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
        if period_lower == "today":
            days_back = 0
        elif period_lower == "yesterday":
            days_back = 1
        if days_back is None:
            days_back = 30
        if extraction.time_range.reference_type == TimeReference.ALL_TIME:
            days_back = QUERY_LIMITS["max_lookback_days"]
        elif extraction.time_range.reference_type == TimeReference.UNSPECIFIED:
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
            has_explicit_credit_intent = any(k in raw_lower for k in ("received", "credited", "income", "salary"))
            if has_explicit_credit_intent:
                transaction_type = "credit"

            is_expense_query = not has_explicit_credit_intent and effective_intent in (
                ExtractionIntent.SPENDING_TOTAL,
                ExtractionIntent.CATEGORY_BREAKDOWN,
            )

            if not is_expense_query and raw_lower:
                if any(k in raw_lower for k in ("spending", "expense", "spent", "cost", "paid")):
                    is_expense_query = True

            if is_expense_query:
                transaction_type = "debit"

        if transaction_type in {"credit", "debit"}:
            return cast(Literal["credit", "debit"], transaction_type)
        return None

    def _build_filters(self, extraction: "QueryExtractionResult", *, effective_intent: ExtractionIntent) -> Filters | None:
        if not extraction.filters:
            return None

        transaction_type = self._infer_transaction_type(
            extracted_transaction_type=extraction.filters.transaction_type,
            raw_query=extraction.raw_query,
            effective_intent=effective_intent,
        )

        return Filters(
            merchant=[extraction.filters.recipient] if extraction.filters.recipient else None,
            category=[extraction.filters.category] if extraction.filters.category else None,
            min_amount=extraction.filters.min_amount,
            max_amount=extraction.filters.max_amount,
            transaction_type=transaction_type,
            account_filter=extraction.filters.bank,
        )

    @staticmethod
    def _is_singular_superlative_query(raw_query: str | None, *, include_spending: bool) -> bool:
        raw_lower = (raw_query or "").lower()
        if not raw_lower:
            return False

        singular_plural_pairs = [("expense", "expenses"), ("transaction", "transactions")]
        if include_spending:
            singular_plural_pairs.append(("spending", "spendings"))

        is_singular = any(singular in raw_lower and plural not in raw_lower for singular, plural in singular_plural_pairs)
        return is_singular or " one" in raw_lower

    @staticmethod
    def _coerce_aggregation_type(agg_type: str) -> Literal["sum", "average", "count", "largest", "smallest", "breakdown"]:
        return cast(
            Literal["sum", "average", "count", "largest", "smallest", "breakdown"],
            agg_type if agg_type in {"sum", "average", "count", "largest", "smallest", "breakdown"} else "sum",
        )

    @staticmethod
    def _coerce_group_by(group_by: str | None) -> Literal["category", "merchant", "day", "account"] | None:
        if group_by in {"category", "merchant", "day", "account"}:
            return cast(Literal["category", "merchant", "day", "account"], group_by)
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

        limit = extraction.aggregation.limit
        if agg_type in ("largest", "smallest") and self._is_singular_superlative_query(
            extraction.raw_query, include_spending=True
        ):
            limit = 1

        aggregation = Aggregation(
            type=self._coerce_aggregation_type(agg_type),
            group_by=self._coerce_group_by(extraction.aggregation.group_by),
            limit=limit or 5,
        )
        if agg_type == "breakdown" and not aggregation.group_by:
            aggregation.group_by = "category"
        return aggregation

    def _build_default_aggregation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
    ) -> Aggregation | None:
        if effective_intent == ExtractionIntent.SPENDING_TOTAL:
            agg_type = "sum"
            limit = 5
            raw_lower = (extraction.raw_query or "").lower()
            if any(x in raw_lower for x in ("largest", "biggest", "highest", "top")):
                agg_type = "largest"
            elif any(x in raw_lower for x in ("smallest", "least", "lowest")):
                agg_type = "smallest"

            if self._is_singular_superlative_query(extraction.raw_query, include_spending=False):
                limit = 1

            return Aggregation(type=self._coerce_aggregation_type(agg_type), limit=limit)

        if effective_intent == ExtractionIntent.CATEGORY_BREAKDOWN:
            return Aggregation(type="breakdown", group_by="category")

        return None

    def _build_aggregation(
        self,
        extraction: "QueryExtractionResult",
        *,
        effective_intent: ExtractionIntent,
    ) -> Aggregation | None:
        extracted_aggregation = self._build_aggregation_from_extracted(extraction, effective_intent=effective_intent)
        if extracted_aggregation is not None:
            return extracted_aggregation
        return self._build_default_aggregation(extraction, effective_intent=effective_intent)

    def convert_to_normalized(
        self,
        extraction: "QueryExtractionResult",
        today: date | None = None,
    ) -> NormalizedQuery:
        """Convert QueryExtractionResult to NormalizedQuery for handlers."""

        today = today or lagos_today()
        effective_intent = self._resolve_effective_intent(extraction)
        result_limit = self._resolve_result_limit(extraction.result_limit, effective_intent=effective_intent)

        intent_map = {
            ExtractionIntent.TRANSACTION_LIST: QueryIntent.TRANSACTION_LIST,
            ExtractionIntent.SPENDING_TOTAL: QueryIntent.ANALYTICS_SUMMARY,
            ExtractionIntent.CATEGORY_BREAKDOWN: QueryIntent.ANALYTICS_SUMMARY,
            ExtractionIntent.TIME_COMPARISON: QueryIntent.TIME_COMPARISON,
            ExtractionIntent.SINGLE_TRANSACTION: QueryIntent.TRANSACTION_SEARCH,
            ExtractionIntent.AFFORDABILITY: QueryIntent.AFFORDABILITY,
        }

        time_range = self._build_time_range(extraction, today=today)
        filters = self._build_filters(extraction, effective_intent=effective_intent)
        aggregation = self._build_aggregation(extraction, effective_intent=effective_intent)

        return NormalizedQuery(
            intent=intent_map.get(effective_intent, QueryIntent.TRANSACTION_LIST),
            time_range=time_range or TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
            filters=filters,
            aggregation=aggregation,
            accounts_scope="all",
            result_limit=result_limit,
            result_reference=extraction.result_reference,
        )
