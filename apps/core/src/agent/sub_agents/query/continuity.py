"""Conversational continuity for query follow-ups and drill-down.

Handles:
- "What about last month?" (time delta)
- "Only credits" (filter delta)
- "Tell me more about the 150k one" (drill-down)

Uses LLM for multilingual continuation classification.
"""

from typing import Any, Literal

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from apps.core.src.agent.sub_agents.query.models import (
    Filters,
    NormalizedQuery,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from apps.core.src.agent.sub_agents.query.prompts import CONTINUATION_CLASSIFIER_PROMPT
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ContinuationType:
    """Types of follow-up continuations."""

    SHOW_MORE = "show_more"
    TIME_DELTA = "time_delta"
    FILTER_DELTA = "filter_delta"
    DRILL_DOWN = "drill_down"
    NEW_QUERY = "new_query"


class ContinuationClassification(BaseModel):
    """LLM output for continuation classification."""

    continuation_type: Literal["show_more", "time_delta", "filter_delta", "drill_down", "new_query"] = Field(
        description="Type of continuation the user is requesting"
    )

    # For time_delta
    time_range: TimeRange | None = Field(default=None, description="Resolved date range if time_delta")

    # For filter_delta
    filters: Filters | None = Field(default=None, description="Filter modifications if filter_delta")

    # For drill_down
    drill_down_reference: str | None = Field(
        default=None, description="What user is referencing (amount, index, description)"
    )


class ContinuationClassifier:
    """LLM-based continuation classifier for multilingual support."""

    def __init__(self, llm: Runnable):
        self.llm = llm
        self.structured_llm = llm.with_structured_output(ContinuationClassification)

    async def classify(
        self,
        message: str,
        has_active_session: bool,
        today: str,
    ) -> tuple[str, dict[str, Any]]:
        """
        Classify a user message as a continuation type.

        Args:
            message: User's message
            has_active_session: Whether there's an active query session
            today: Today's date in YYYY-MM-DD format

        Returns:
            Tuple of (continuation_type, extracted_data)
        """
        if not has_active_session:
            return ContinuationType.NEW_QUERY, {}

        try:
            prompt = CONTINUATION_CLASSIFIER_PROMPT.format(
                today=today,
                message=message,
            )

            result: ContinuationClassification = await self.structured_llm.ainvoke(prompt)

            data: dict[str, Any] = {}

            if result.continuation_type == "time_delta" and result.time_range:
                data["time_range"] = result.time_range

            elif result.continuation_type == "filter_delta" and result.filters:
                data["filters"] = result.filters

            elif result.continuation_type == "drill_down" and result.drill_down_reference:
                data["reference"] = result.drill_down_reference

            logger.info("continuation_classified", type=result.continuation_type)
            return result.continuation_type, data

        except Exception as e:
            logger.error("continuation_classification_error", error=str(e))
            # Fallback to new query on error
            return ContinuationType.NEW_QUERY, {}


def apply_filter_delta(
    original_query: NormalizedQuery,
    filters: Filters,
) -> NormalizedQuery:
    """
    Apply a filter delta to an existing query.

    Args:
        original_query: The original normalized query
        filters: Filter modifications to apply

    Returns:
        Modified NormalizedQuery
    """
    query_dict = original_query.model_dump()
    existing_filters = query_dict.get("filters") or {}

    # Merge new filters with existing
    new_filters = filters.model_dump(exclude_none=True)
    for key, value in new_filters.items():
        if key == "exclude" and existing_filters.get("exclude"):
            # Append to existing excludes
            existing_filters["exclude"] = existing_filters["exclude"] + value
        else:
            existing_filters[key] = value

    query_dict["filters"] = existing_filters
    return NormalizedQuery.model_validate(query_dict)


def apply_time_delta(
    original_query: NormalizedQuery,
    time_range: TimeRange,
) -> NormalizedQuery:
    """
    Apply a time range change to an existing query.

    Args:
        original_query: The original normalized query
        time_range: New time range

    Returns:
        Modified NormalizedQuery
    """
    query_dict = original_query.model_dump()
    query_dict["time_range"] = time_range.model_dump()
    return NormalizedQuery.model_validate(query_dict)


def resolve_drill_down(
    reference: str,
    last_result: QueryResult,
) -> QueryResultItem | None:
    """
    Resolve a drill-down reference against the last query result.

    Args:
        reference: What the user is referencing (from LLM)
        last_result: The last QueryResult to search in

    Returns:
        The matched QueryResultItem or None
    """
    if not last_result.items:
        return None

    items = last_result.items
    ref_lower = reference.lower()

    # Try to extract amount reference (e.g., "150k", "50000")
    amount = _try_parse_amount(ref_lower)
    if amount:
        # Find item with closest amount
        for item in items:
            if abs(item.amount - amount) / max(amount, 1) < 0.1:
                return item
        # Return closest match
        return min(items, key=lambda i: abs(i.amount - amount))

    # Try ordinal/index reference
    ordinal_map = {
        "first": 0,
        "1st": 0,
        "1": 0,
        "second": 1,
        "2nd": 1,
        "2": 1,
        "third": 2,
        "3rd": 2,
        "3": 2,
        "fourth": 3,
        "4th": 3,
        "4": 3,
        "fifth": 4,
        "5th": 4,
        "5": 4,
        "last": -1,
    }
    for key, idx in ordinal_map.items():
        if key in ref_lower:
            if idx == -1:
                return items[-1]
            if idx < len(items):
                return items[idx]

    # Try description match
    for item in items:
        if ref_lower in item.description.lower():
            return item

    # Default to first item
    return items[0] if items else None


def _try_parse_amount(text: str) -> float | None:
    """Try to parse an amount from text."""
    import re

    # Match patterns like "150k", "50000", "₦150,000"
    patterns = [
        r"(\d+)k\b",  # 150k
        r"(\d{1,3}(?:,\d{3})+)",  # 150,000
        r"(\d+)",  # 50000
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).replace(",", "")
            amount = float(value)
            if "k" in text.lower():
                amount *= 1000
            if amount >= 100:  # Likely an amount, not an index
                return amount

    return None
