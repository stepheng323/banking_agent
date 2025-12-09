"""Query parsing service to extract parameters from natural language questions."""

import json
from typing import Dict, Any
from datetime import datetime, timedelta
from langchain_core.runnables import Runnable
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
        prompt = self._build_prompt(question)
        
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
            
            return params
            
        except Exception as e:
            logger.error("error_parsing")
            return self._get_default_params()
    
    def _build_prompt(self, question: str) -> str:
        """Build prompt for LLM."""
        today = datetime.now().strftime("%Y-%m-%d")
        
        return f"""
Parse this financial query into structured parameters.

Today's date: {today}

Question: {question}

Extract:
1. query_type: total_spent | top_recipient | top_sender | breakdown | search | transaction_list
2. transaction_type: debit | credit | both
3. date_range: {{from: YYYY-MM-DD, to: YYYY-MM-DD}}
4. narration_filter: string (if searching for specific merchant/person)
5. group_by: category | recipient | sender | date | none
6. limit: int (default: 10)

Return ONLY valid JSON in this format:
{{
  "query_type": "total_spent",
  "transaction_type": "debit",
  "date_range": {{
    "from": "YYYY-MM-DD",
    "to": "YYYY-MM-DD"
  }},
  "narration_filter": null,
  "group_by": "none",
  "limit": 10
}}
"""
    
    def _add_defaults(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add default values for missing parameters."""
        today = datetime.now()
        
        defaults = {
            "query_type": "transaction_list",
            "transaction_type": "both",
            "date_range": {
                "from": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
                "to": today.strftime("%Y-%m-%d")
            },
            "narration_filter": None,
            "group_by": "none",
            "limit": 10
        }
        
        for key, value in defaults.items():
            if key not in params:
                params[key] = value
        
        return params
    
    def _get_default_params(self) -> Dict[str, Any]:
        """Get default parameters for fallback."""
        today = datetime.now()
        return {
            "query_type": "transaction_list",
            "transaction_type": "both",
            "date_range": {
                "from": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
                "to": today.strftime("%Y-%m-%d")
            },
            "narration_filter": None,
            "group_by": "none",
            "limit": 10
        }
