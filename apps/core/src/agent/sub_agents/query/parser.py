"""Query parsing service to extract parameters from natural language questions."""

from typing import Dict, Any
from datetime import datetime, timedelta
from langchain_core.runnables import Runnable

from apps.core.src.agent.sub_agents.query.models import QueryParams
from shared.utils.logging import get_logger

logger = get_logger(__name__)


QUERY_PARSER_PROMPT = """Parse this financial query into structured parameters.

Today's date: {today}
Question: {question}

QUERY TYPES:
- balance: asking about current balance
- total_spent: asking how much was spent/debited
- total_received: asking how much was received/credited
- transaction_list: show list of transactions
- search: find specific transactions by name/merchant
- top_recipient: who received most money from user
- top_sender: who sent most money to user
- affordability: checking if user can afford a specific amount (extract the amount)

DATE EXPRESSIONS:
- "today" → today's date
- "yesterday" → yesterday
- "this week" → last 7 days
- "this month" → current month
- "last month" → previous month
- "last 30 days" → past 30 days

AFFORDABILITY EXAMPLES:
- "Can I afford 80k?" → query_type: affordability, amount_check: 80000
- "Do I have enough for 50,000?" → query_type: affordability, amount_check: 50000
- "Can I spend 100k right now?" → query_type: affordability, amount_check: 100000

Extract the query parameters from the user's question."""


class QueryParser:
    """Parse natural language financial questions into structured parameters."""

    def __init__(self, llm: Runnable):
        self.llm = llm
        self.structured_llm = llm.with_structured_output(QueryParams)

    async def parse(self, question: str) -> Dict[str, Any]:
        """Parse a financial question into query parameters."""
        today = datetime.now()
        prompt = QUERY_PARSER_PROMPT.format(
            today=today.strftime("%Y-%m-%d"),
            question=question
        )

        try:
            result: QueryParams = await self.structured_llm.ainvoke(prompt)
            params = result.model_dump()
            params = self._add_defaults(params)
            logger.info("query_parsed", query_type=params.get("query_type"))
            return params

        except Exception as e:
            logger.error("query_parse_error", error=str(e))
            return self._get_default_params()

    def _add_defaults(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add default values for missing parameters."""
        today = datetime.now()

        if not params.get("date_range"):
            params["date_range"] = {
                "start": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
                "end": today.strftime("%Y-%m-%d")
            }
        elif isinstance(params["date_range"], dict):
            if not params["date_range"].get("start"):
                params["date_range"]["start"] = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            if not params["date_range"].get("end"):
                params["date_range"]["end"] = today.strftime("%Y-%m-%d")

        return params

    def _get_default_params(self) -> Dict[str, Any]:
        """Get default parameters for fallback."""
        today = datetime.now()
        return {
            "query_type": "transaction_list",
            "transaction_type": "both",
            "date_range": {
                "start": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
                "end": today.strftime("%Y-%m-%d")
            },
            "narration_filter": None,
            "limit": 10
        }
