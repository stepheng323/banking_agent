"""Query parsing service - extracts NormalizedQuery from natural language."""

from datetime import date, timedelta
from typing import Any, Literal, cast

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.capabilities import (
    QUERY_LIMITS,
)
from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    NormalizedQuery,
    QueryExtractionResult,
    QueryIntent,
    QueryParseResult,
    ResolverOutcome,
    TimeRange,
    TimeReference,
)
from apps.core.src.agent.graphs.query.prompts import QUERY_PARSER_PROMPT
from apps.core.src.agent.graphs.query.services.resolver import Decision, resolve
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryParser:
    """Parse natural language financial questions into NormalizedQuery."""

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
        from apps.core.src.agent.graphs.query.models import (
            QueryParseResult,
        )

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

            return QueryParseResult(
                outcome=outcome,
                extraction=decision.extraction,
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

    def convert_to_normalized(
        self,
        extraction: "QueryExtractionResult",
        today: date | None = None,
    ) -> NormalizedQuery:
        """Convert QueryExtractionResult to NormalizedQuery for handlers."""

        from apps.core.src.agent.graphs.query.models import (
            ExtractionIntent,
        )

        today = today or date.today()

        result_limit = extraction.result_limit
        if extraction.intent == ExtractionIntent.SINGLE_TRANSACTION:
            result_limit = result_limit or 1

        if result_limit:
            result_limit = min(result_limit, QUERY_LIMITS["max_results"])

        intent_map = {
            ExtractionIntent.TRANSACTION_LIST: QueryIntent.TRANSACTION_LIST,
            ExtractionIntent.SPENDING_TOTAL: QueryIntent.ANALYTICS_SUMMARY,
            ExtractionIntent.CATEGORY_BREAKDOWN: QueryIntent.ANALYTICS_SUMMARY,
            ExtractionIntent.TIME_COMPARISON: QueryIntent.TIME_COMPARISON,
            ExtractionIntent.SINGLE_TRANSACTION: QueryIntent.TRANSACTION_SEARCH,
            ExtractionIntent.AFFORDABILITY: QueryIntent.AFFORDABILITY,
        }

        time_range = None
        if extraction.time_range:
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

            time_range = TimeRange(
                start=today - timedelta(days=days_back),
                end=today,
                granularity="day",
            )

        filters = None
        if extraction.filters:
            from apps.core.src.agent.graphs.query.models import Filters

            transaction_type = (extraction.filters.transaction_type or "").strip().lower() or None
            if not transaction_type:
                raw_lower = (extraction.raw_query or "").lower()
                has_explicit_credit_intent = any(k in raw_lower for k in ("received", "credited", "income", "salary"))
                if has_explicit_credit_intent:
                    transaction_type = "credit"

                # Force debit for specific intents OR if keywords are present
                is_expense_query = not has_explicit_credit_intent and extraction.intent in (
                    ExtractionIntent.SPENDING_TOTAL,
                    ExtractionIntent.CATEGORY_BREAKDOWN,
                )

                # Check for expense keywords in raw query if not already explicit
                if not is_expense_query and raw_lower:
                    if any(k in raw_lower for k in ("spending", "expense", "spent", "cost", "paid")):
                        is_expense_query = True

                if is_expense_query:
                    transaction_type = "debit"

            typed_transaction_type = (
                cast(Literal["credit", "debit"], transaction_type) if transaction_type in {"credit", "debit"} else None
            )

            filters = Filters(
                merchant=[extraction.filters.recipient] if extraction.filters.recipient else None,
                category=[extraction.filters.category] if extraction.filters.category else None,
                min_amount=extraction.filters.min_amount,
                max_amount=extraction.filters.max_amount,
                transaction_type=typed_transaction_type,
                account_filter=extraction.filters.bank,
            )

        aggregation = None
        if extraction.aggregation:
            agg_type = extraction.aggregation.type or "sum"
            # Enforce breakdown type if intent matches, correcting LLM 'sum' hallucination
            if extraction.intent == ExtractionIntent.CATEGORY_BREAKDOWN and agg_type == "sum":
                agg_type = "breakdown"

            # Validate limit
            limit = extraction.aggregation.limit

            # Programmatic fallback for singular superlatives if limit is missing or >1
            if agg_type in ("largest", "smallest") and extraction.raw_query:
                raw_lower = extraction.raw_query.lower()
                # If singular "expense" or "transaction" appearing without "s" at end
                # Heuristic: check if "expense" is present but "expenses" is NOT (or similar for transaction)

                is_singular = False
                for singular, plural in [
                    ("expense", "expenses"),
                    ("transaction", "transactions"),
                    ("spending", "spendings"),
                ]:
                    if singular in raw_lower and plural not in raw_lower:
                        is_singular = True
                        break

                # Also check "largest one", "top one"
                if " one" in raw_lower:
                    is_singular = True

                if is_singular:
                    limit = 1

            typed_agg_type = cast(
                Literal["sum", "average", "count", "largest", "smallest", "breakdown"],
                agg_type if agg_type in {"sum", "average", "count", "largest", "smallest", "breakdown"} else "sum",
            )
            typed_group_by = (
                cast(Literal["category", "merchant", "day", "account"], extraction.aggregation.group_by)
                if extraction.aggregation.group_by in {"category", "merchant", "day", "account"}
                else None
            )

            aggregation = Aggregation(
                type=typed_agg_type,
                group_by=typed_group_by,
                limit=limit or 5,  # Default to 5 if still None
            )

            # Default group_by for breakdown if missing
            if agg_type == "breakdown" and not aggregation.group_by:
                aggregation.group_by = "category"

        elif extraction.intent == ExtractionIntent.SPENDING_TOTAL:
            # Check for largest/smallest/top keywords in raw query to upgrade intent
            agg_type = "sum"
            limit = 5

            if extraction.raw_query:
                raw_lower = extraction.raw_query.lower()
                if any(x in raw_lower for x in ("largest", "biggest", "highest", "top")):
                    agg_type = "largest"
                elif any(x in raw_lower for x in ("smallest", "least", "lowest")):
                    agg_type = "smallest"

                # Check singular
                is_singular = False
                for singular, plural in [("expense", "expenses"), ("transaction", "transactions")]:
                    if singular in raw_lower and plural not in raw_lower:
                        is_singular = True
                        break

                if is_singular:
                    limit = 1

            typed_agg_type = cast(
                Literal["sum", "average", "count", "largest", "smallest", "breakdown"],
                agg_type if agg_type in {"sum", "average", "count", "largest", "smallest", "breakdown"} else "sum",
            )
            aggregation = Aggregation(type=typed_agg_type, limit=limit)
        elif extraction.intent == ExtractionIntent.CATEGORY_BREAKDOWN:
            aggregation = Aggregation(type="breakdown", group_by="category")

        return NormalizedQuery(
            intent=intent_map.get(extraction.intent, QueryIntent.TRANSACTION_LIST),
            time_range=time_range or TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
            filters=filters,
            aggregation=aggregation,
            accounts_scope="all",
            result_limit=result_limit,
            result_reference=extraction.result_reference,
        )
