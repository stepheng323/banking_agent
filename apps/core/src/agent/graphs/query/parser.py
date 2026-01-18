"""Query parsing service - extracts NormalizedQuery from natural language."""

from datetime import date, timedelta
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.models import (
    Aggregation,
    NormalizedQuery,
    QueryIntent,
    TimeRange,
)
from apps.core.src.agent.graphs.query.prompts import QUERY_PARSER_PROMPT
from shared.utils.logging import get_logger

from apps.core.src.agent.graphs.query.capabilities import (
    CAPABILITY_LABELS,
    QueryCapability,
    QUERY_SUPPORTS,
    QUERY_LIMITS,
    get_alternative,
    generate_limitation_message,
)
from apps.core.src.agent.graphs.query.models_extraction import QueryExtractionResult
from apps.core.src.agent.graphs.query.prompts_extraction import QUERY_EXTRACTION_PROMPT
from apps.core.src.agent.graphs.query.resolver import resolve, Decision

logger = get_logger(__name__)


class QueryParser:
    """Parse natural language financial questions into NormalizedQuery."""

    def __init__(self, llm: Runnable):
        self.llm = llm
        self.structured_llm = llm.with_structured_output(NormalizedQuery)

    async def parse(
        self,
        question: str,
        message_id: str | None = None,
    ) -> tuple["QueryExtractionResult", str | None]:
        """
        Parse query using extraction with resolver integration.
        
        Returns:
            Tuple of (extraction_result, resolver_message)
            - resolver_message is set if negotiation/clamping occurred
        """
        today = date.today()
        prompt = QUERY_EXTRACTION_PROMPT.format(
            today=today.isoformat(),
            question=question,
        )
        
        structured_llm = self.llm.with_structured_output(QueryExtractionResult)
        
        try:
            extraction: QueryExtractionResult = await structured_llm.ainvoke(prompt)
            extraction.raw_query = question
            
            # Run through resolver
            decision = resolve(extraction)
            
            if decision.decision == Decision.ASK_CLARIFY:
                # Return with clarification prompt
                clarify_msg = decision.prompts[0].vars.get("context", "Could you clarify?") if decision.prompts else "Could you clarify?"
                return extraction, f"clarify:{clarify_msg}"
            
            if decision.decision == Decision.NEGOTIATE:
                return decision.extraction, f"negotiate:{decision.negotiation.message}" if decision.negotiation else None
            
            resolver_msg = None
            if decision.clamped.days_back:
                resolver_msg = f"Showing last {decision.clamped.days_back} days (max available)."
            
            return decision.extraction, resolver_msg
            
        except Exception as e:
            logger.error("parse_error", error=str(e))
            return QueryExtractionResult(raw_query=question), None

    async def parse(
        self,
        question: str,
        message_id: str | None = None,
    ) -> tuple["QueryExtractionResult", str | None]:
        """
        Parse query using extraction with resolver integration.
        
        Returns:
            Tuple of (extraction_result, resolver_message)
            - resolver_message is set if negotiation/clamping occurred
        """
        today = date.today()
        prompt = QUERY_EXTRACTION_PROMPT.format(
            today=today.isoformat(),
            question=question,
        )
        
        structured_llm = self.llm.with_structured_output(QueryExtractionResult)
        
        try:
            extraction: QueryExtractionResult = await structured_llm.ainvoke(prompt)
            extraction.raw_query = question
            
            decision = resolve(extraction)
            
            if decision.decision == Decision.ASK_CLARIFY:
                clarify_msg = decision.prompts[0].vars.get("context", "Could you clarify?") if decision.prompts else "Could you clarify?"
                return extraction, f"clarify:{clarify_msg}"
            
            if decision.decision == Decision.NEGOTIATE:
                return decision.extraction, f"negotiate:{decision.negotiation.message}" if decision.negotiation else None
            
            resolver_msg = None
            if decision.clamped.days_back:
                resolver_msg = f"Showing last {decision.clamped.days_back} days (max available)."
            
            return decision.extraction, resolver_msg
            
        except Exception as e:
            logger.error("parse_error", error=str(e))
            return QueryExtractionResult(raw_query=question), None

    def convert_to_normalized(
        self,
        extraction: "QueryExtractionResult",
        today: date | None = None,
    ) -> NormalizedQuery:
        """Convert QueryExtractionResult to NormalizedQuery for handlers."""
        from datetime import timedelta
        from apps.core.src.agent.graphs.query.models_extraction import (
            QueryExtractionResult,
            QueryIntent as ExtractIntent,
            TimeReference,
        )
        
        today = today or date.today()
        
        intent_map = {
            ExtractIntent.TRANSACTION_LIST: QueryIntent.TRANSACTION_LIST,
            ExtractIntent.SPENDING_TOTAL: QueryIntent.ANALYTICS_SUMMARY,
            ExtractIntent.CATEGORY_BREAKDOWN: QueryIntent.ANALYTICS_SUMMARY,
            ExtractIntent.TIME_COMPARISON: QueryIntent.TIME_COMPARISON,
            ExtractIntent.BALANCE_CHECK: QueryIntent.BALANCE_QUERY,
            ExtractIntent.SINGLE_TRANSACTION: QueryIntent.TRANSACTION_SEARCH,
            ExtractIntent.AFFORDABILITY: QueryIntent.AFFORDABILITY,
        }
        
        time_range = None
        if extraction.time_range:
            days_back = extraction.time_range.days_back or 30
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
            filters = Filters(
                merchant=[extraction.filters.recipient] if extraction.filters.recipient else None,
                category=[extraction.filters.category] if extraction.filters.category else None,
                min_amount=extraction.filters.min_amount,
                max_amount=extraction.filters.max_amount,
                transaction_type=extraction.filters.transaction_type,
                account_filter=extraction.filters.bank,
            )
        
        aggregation = None
        if extraction.aggregation:
            aggregation = Aggregation(
                type=extraction.aggregation.type or "sum",
                group_by=extraction.aggregation.group_by,
            )
        
        return NormalizedQuery(
            intent=intent_map.get(extraction.intent, QueryIntent.TRANSACTION_LIST),
            time_range=time_range or TimeRange(start=today - timedelta(days=30), end=today, granularity="day"),
            filters=filters,
            aggregation=aggregation,
            accounts_scope="all",
        )

