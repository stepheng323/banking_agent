"""Query extraction models. Pure extraction with requested_capabilities."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Schema version for future-proofing
SCHEMA_VERSION = 1


class ExtractionIntent(str, Enum):
    """Query intent types."""

    TRANSACTION_LIST = "transaction_list"  # Show me transactions
    SPENDING_TOTAL = "spending_total"  # How much did I spend
    CATEGORY_BREAKDOWN = "category_breakdown"  # Breakdown by category
    TIME_COMPARISON = "time_comparison"  # Compare periods
    SINGLE_TRANSACTION = "single_transaction"  # Find specific transaction
    AFFORDABILITY = "affordability"  # Can I afford X


class RequestedCapability(str, Enum):
    """Capabilities the user is requesting (LLM detects these)."""

    FILTER_RECIPIENT = "FILTER_RECIPIENT"
    FILTER_AMOUNT = "FILTER_AMOUNT"
    FILTER_CATEGORY = "FILTER_CATEGORY"
    FILTER_TX_TYPE = "FILTER_TX_TYPE"
    FILTER_BANK = "FILTER_BANK"
    SEARCH_NARRATION_KEYWORD = "SEARCH_NARRATION_KEYWORD"
    SEARCH_NARRATION_FUZZY = "SEARCH_NARRATION_FUZZY"
    TIME_RELATIVE = "TIME_RELATIVE"
    TIME_ALL = "TIME_ALL"
    AGGREGATE_SUM = "AGGREGATE_SUM"
    AGGREGATE_GROUP = "AGGREGATE_GROUP"
    TIME_COMPARISON = "TIME_COMPARISON"
    EXPORT_PDF = "EXPORT_PDF"
    EXPORT_CSV = "EXPORT_CSV"


class TimeReference(str, Enum):
    """How user expressed time."""

    EXPLICIT = "explicit"  # "last week", "this month", "January"
    VAGUE = "vague"  # "sometime ago", "recently", "a while back"
    ALL_TIME = "all_time"  # "all my transactions", "everything"
    UNSPECIFIED = "unspecified"  # No time mentioned


class AmbiguityCode(str, Enum):
    """Ambiguity types for query."""

    TIME_VAGUE = "TIME_VAGUE"  # "sometime ago" - unclear when
    RECIPIENT_VAGUE = "RECIPIENT_VAGUE"  # "that mechanic" - unclear who
    AMOUNT_VAGUE = "AMOUNT_VAGUE"  # "large transactions" - unclear threshold


class Ambiguity(BaseModel):
    """Structured ambiguity with context."""

    code: AmbiguityCode = Field(description="Ambiguity type")
    context: str | None = Field(default=None, description="What user said")
    suggestion: str | None = Field(default=None, description="Resolver can suggest")


class QueryFilters(BaseModel):
    """Extracted filters from query."""

    recipient: str | None = Field(default=None, description="Who to filter by")
    min_amount: float | None = Field(default=None)
    max_amount: float | None = Field(default=None)
    category: str | None = Field(default=None)
    transaction_type: str | None = Field(default=None, description="credit/debit")
    bank: str | None = Field(default=None)
    narration_keyword: str | None = Field(default=None, description="Keyword to search")


class QueryTimeRange(BaseModel):
    """Time range extracted from query."""

    reference_type: TimeReference = Field(default=TimeReference.UNSPECIFIED)
    period: str | None = Field(default=None, description="'last_week', 'this_month', 'january'")
    days_back: int | None = Field(default=None, description="Estimated days if vague")


class QueryAggregation(BaseModel):
    """Aggregation requested."""

    type: str | None = Field(default=None, description="sum, count, average, largest")
    group_by: str | None = Field(default=None, description="category, bank, recipient")


class QueryExtractionResult(BaseModel):
    """Pure query extraction with requested_capabilities."""

    schema_version: int = Field(default=SCHEMA_VERSION)
    intent: ExtractionIntent = Field(default=ExtractionIntent.TRANSACTION_LIST)
    intent_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    filters: QueryFilters = Field(default_factory=QueryFilters)
    time_range: QueryTimeRange = Field(default_factory=QueryTimeRange)
    aggregation: QueryAggregation | None = Field(default=None)

    requested_capabilities: list[RequestedCapability] = Field(
        default_factory=list,
        description="Capabilities needed for this query (LLM detects)",
    )

    ambiguities: list[Ambiguity] = Field(
        default_factory=list,
        description="Detected ambiguities needing clarification",
    )

    raw_query: str | None = Field(default=None, description="Original user query")


class ResolverOutcome(str, Enum):
    """Outcome of the resolution process."""

    OK = "OK"  # Proceed with query
    NEEDS_INPUT = "NEEDS_INPUT"  # Ask user for clarification
    NEGOTIATED = "NEGOTIATED"  # Clamped or modified, but proceeding


class QueryParseResult(BaseModel):
    """Structured result from the Query Parser."""

    outcome: ResolverOutcome
    extraction: QueryExtractionResult | None = None
    resolver_message: str | None = Field(default=None, description="Message to show user (e.g. clarification)")
    notices: list[str] = Field(default_factory=list, description="Infos like 'Clamped to 30 days'")
    patch: dict[str, Any] | None = Field(default=None, description="State updates")
