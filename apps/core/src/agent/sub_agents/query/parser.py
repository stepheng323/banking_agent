"""Query parsing service to extract parameters from natural language questions."""

import json
from typing import Dict, Any
from datetime import datetime, timedelta
from langchain_core.runnables import Runnable
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

DATE EXPRESSIONS:
- "today" → today's date
- "yesterday" → yesterday
- "this week" → last 7 days
- "this month" → current month
- "last month" → previous month
- "last 30 days" → past 30 days

Extract:
1. query_type: balance | total_spent | total_received | transaction_list | search | top_recipient | top_sender
2. transaction_type: debit | credit | both
3. date_range: {{start: YYYY-MM-DD, end: YYYY-MM-DD}}
4. narration_filter: string (merchant/person name to search for, null if none)
5. limit: int (default: 10)

Return ONLY valid JSON. Do NOT include markdown code blocks.

{{
  "query_type": "...",
  "transaction_type": "...",
  "date_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}},
  "narration_filter": null,
  "limit": 10
}}
"""


class QueryParser:
    """Parse natural language financial questions into structured parameters."""

    def __init__(self, llm: Runnable):
        """
        Initialize query parser.

        Args:
            llm: Language model for parsing
        """
        self.llm = llm

    async def parse(self, question: str) -> Dict[str, Any]:
        """
        Parse a financial question into query parameters.

        Args:
            question: Natural language question

        Returns:
            Dictionary with query parameters
        """
        today = datetime.now()
        prompt = QUERY_PARSER_PROMPT.format(
            today=today.strftime("%Y-%m-%d"),
            question=question
        )

        try:
            result = await self.llm.ainvoke(prompt)

            # Parse JSON response
            if hasattr(result, 'content'):
                content = result.content
            else:
                content = str(result)

            # Extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                params = json.loads(json_match.group())
            else:
                params = json.loads(content)

            # Add defaults
            params = self._add_defaults(params)

            logger.info("query_parsed", query_type=params.get("query_type"))
            return params

        except Exception as e:
            logger.error("query_parse_error", error=str(e))
            return self._get_default_params()

    def _add_defaults(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add default values for missing parameters."""
        today = datetime.now()

        defaults = {
            "query_type": "transaction_list",
            "transaction_type": "both",
            "date_range": {
                "start": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
                "end": today.strftime("%Y-%m-%d")
            },
            "narration_filter": None,
            "limit": 10
        }

        for key, value in defaults.items():
            if key not in params or params[key] is None:
                params[key] = value

        # Ensure date_range has both start and end
        if "date_range" in params and params["date_range"]:
            if not params["date_range"].get("start"):
                params["date_range"]["start"] = defaults["date_range"]["start"]
            if not params["date_range"].get("end"):
                params["date_range"]["end"] = defaults["date_range"]["end"]

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
