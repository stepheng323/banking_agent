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
    check_capabilities,
    derive_requirements,
    get_alternatives,
)

logger = get_logger(__name__)


class QueryParser:
    """Parse natural language financial questions into NormalizedQuery."""

    def __init__(self, llm: Runnable):
        self.llm = llm
        self.structured_llm = llm.with_structured_output(NormalizedQuery)

    async def parse(self, question: str, message_id: str | None = None) -> NormalizedQuery:
        """
        Parse a financial question into a NormalizedQuery.

        Args:
            question: User's natural language query
            message_id: Optional message ID for tracing

        Returns:
            NormalizedQuery with resolved dates and extracted parameters
        """
        today = date.today()
        prompt = QUERY_PARSER_PROMPT.format(
            today=today.isoformat(),
            question=question,
        )

        try:
            result: NormalizedQuery = await self.structured_llm.ainvoke(prompt)

            if message_id:
                result = result.model_copy(update={"source_message_id": message_id})

            result = self._add_defaults(result, today)

            logger.info("query_parsed", intent=result.intent.value)
            return result

        except Exception as e:
            logger.error("query_parse_error", error=str(e))
            return self._get_default_query(today)

    def _add_defaults(self, query: NormalizedQuery, today: date) -> NormalizedQuery:
        """Add default values for missing parameters."""
        updates: dict[str, Any] = {}

        if not query.time_range:
            updates["time_range"] = TimeRange(
                start=today - timedelta(days=30),
                end=today,
                granularity="day",
            )

        if query.intent == QueryIntent.ANALYTICS_SUMMARY and not query.aggregation:
            updates["aggregation"] = Aggregation(type="sum", limit=10)

        if query.intent == QueryIntent.BENEFICIARY_SUMMARY and not query.aggregation:
            updates["aggregation"] = Aggregation(type="sum", group_by="merchant", limit=5)

        if updates:
            return query.model_copy(update=updates)
        return query

    def _get_default_query(self, today: date) -> NormalizedQuery:
        """Get default query for fallback."""
        return NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(
                start=today - timedelta(days=30),
                end=today,
                granularity="day",
            ),
            accounts_scope="all",
        )

    async def parse_with_validation(
        self,
        question: str,
        message_id: str | None = None,
    ) -> tuple[NormalizedQuery | None, str | None]:
        """
        Parse query with validation, returning clarification request if needed.

        Returns:
            Tuple of (query, clarification_message)
            - If successful: (query, None)
            - If clarification needed: (None, clarification_message)
        """
        query = await self.parse(question, message_id)

        if query.intent == QueryIntent.AFFORDABILITY:
            if not query.amount_check and not query.item_name:
                return None, "How much would you like to check? Please specify an amount."
        if query.intent == QueryIntent.TIME_COMPARISON:
            if not query.time_range:
                return None, "What time period would you like to compare?"


        requires = derive_requirements(query)
        missing = check_capabilities(requires)

        if missing:
            alternatives = get_alternatives(missing)
            missing_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in missing]
            alt_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in alternatives]

            logger.info(
                "query_capability_limitation",
                missing=[cap.value for cap in missing],
                alternatives=[cap.value for cap in alternatives],
            )

            msg = f"Got it — you want *{missing_labels[0]}*.\n\n"
            msg += f"This isn't available yet."
            if alt_labels:
                msg += f" I can do *{alt_labels[0]}* instead."
            msg += "\n\nWant me to show that?"

            return None, msg

        return query, None
