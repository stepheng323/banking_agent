"""Query extraction models for parser and reasoner outputs."""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.chat.src.agent.graphs.query.models.domain import QueryFactField, QueryOperation

# Schema version for future-proofing
SCHEMA_VERSION = 1


class ExtractionIntent(str, Enum):
    """Query intent types."""

    TRANSACTION_LIST = "transaction_list"  # Show me transactions
    SPENDING_TOTAL = "spending_total"  # How much did I spend
    CATEGORY_BREAKDOWN = "category_breakdown"  # Breakdown by category
    BENEFICIARY_SUMMARY = "beneficiary_summary"  # Top recipients
    TIME_COMPARISON = "time_comparison"  # Compare periods
    SINGLE_TRANSACTION = "single_transaction"  # Find specific transaction
    AFFORDABILITY = "affordability"  # Can I afford X


class QueryRequestShape(str, Enum):
    """High-level answer shape the user is asking for."""

    FACT = "fact"
    EXISTENCE = "existence"
    DETAIL = "detail"
    LIST = "list"
    GROUPED_SUMMARY = "grouped_summary"
    ANALYTICS = "analytics"
    COMPARISON = "comparison"
    AFFORDABILITY = "affordability"


class FactQueryKind(str, Enum):
    """Specific fact a singular transaction question is asking for."""

    DATE = "date"
    COUNTERPARTY = "counterparty"
    AMOUNT = "amount"
    BANK = "bank"
    STATUS = "status"
    DESCRIPTION = "description"
    REFERENCE = "reference"
    ACCOUNT = "account"
    DIRECTION = "direction"
    CATEGORY = "category"


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

    type: str | None = Field(default=None, description="sum, count, average, largest, smallest")
    group_by: str | None = Field(default=None, description="category, bank, recipient")
    limit: int | None = Field(default=None, description="Max items (1 for singular, N for plural)")
    sort_by: Literal["amount", "count"] | None = Field(
        default=None,
        description="Ranking basis for grouped results, especially beneficiary summaries",
    )


class QueryComparison(BaseModel):
    """Structured comparison directive for time-comparison queries."""

    mode: Literal["previous_equivalent", "year_ago", "explicit_period"] = Field(default="previous_equivalent")
    period: str | None = Field(default=None, description="Explicit comparison period when mode=explicit_period")


class ParserQueryExtraction(BaseModel):
    """Minimal parser-only extraction returned by the fresh-query LLM path."""

    intent: ExtractionIntent = Field(default=ExtractionIntent.TRANSACTION_LIST)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    time_range: QueryTimeRange = Field(default_factory=QueryTimeRange)
    comparison: QueryComparison | None = Field(default=None)
    aggregation: QueryAggregation | None = Field(default=None)
    request_shape: QueryRequestShape | None = Field(default=None)
    fact_query_kind: FactQueryKind | None = Field(default=None)
    result_limit: int | None = Field(default=None, ge=1, le=100, description="Max results to return")
    result_reference: Literal["latest", "oldest"] | None = Field(
        default=None,
        description="Relative positioning for results when user asks for most recent/oldest",
    )
    answer_fact_field: QueryFactField | None = Field(default=None)


class QueryExtractionResult(BaseModel):
    """Pure query extraction with requested_capabilities."""

    schema_version: int = Field(default=SCHEMA_VERSION)
    intent: ExtractionIntent = Field(default=ExtractionIntent.TRANSACTION_LIST)
    query_operation: QueryOperation | None = Field(default=None)
    intent_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    filters: QueryFilters = Field(default_factory=QueryFilters)
    time_range: QueryTimeRange = Field(default_factory=QueryTimeRange)
    comparison: QueryComparison | None = Field(default=None)
    aggregation: QueryAggregation | None = Field(default=None)
    request_shape: QueryRequestShape | None = Field(default=None)
    fact_query_kind: FactQueryKind | None = Field(default=None)
    result_limit: int | None = Field(default=None, ge=1, le=100, description="Max results to return")
    result_reference: Literal["latest", "oldest"] | None = Field(
        default=None,
        description="Relative positioning for results when user asks for most recent/oldest",
    )
    answer_fact_field: QueryFactField | None = Field(default=None)

    requested_capabilities: list[RequestedCapability] = Field(
        default_factory=list,
        description="Capabilities needed for this query (LLM detects)",
    )

    ambiguities: list[Ambiguity] = Field(
        default_factory=list,
        description="Detected ambiguities needing clarification",
    )

    raw_query: str | None = Field(default=None, description="Original user query")


class ReasonerQueryExtraction(BaseModel):
    """Minimal extraction returned by the semantic reasoner on follow-up turns."""

    intent: ExtractionIntent = Field(default=ExtractionIntent.TRANSACTION_LIST)
    query_operation: QueryOperation | None = Field(default=None)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    time_range: QueryTimeRange = Field(default_factory=QueryTimeRange)
    comparison: QueryComparison | None = Field(default=None)
    aggregation: QueryAggregation | None = Field(default=None)
    request_shape: QueryRequestShape | None = Field(default=None)
    fact_query_kind: FactQueryKind | None = Field(default=None)
    result_limit: int | None = Field(default=None, ge=1, le=100)
    result_reference: Literal["latest", "oldest"] | None = Field(default=None)
    answer_fact_field: QueryFactField | None = Field(default=None)
    raw_query: str | None = Field(default=None)

    def to_query_extraction_result(self) -> "QueryExtractionResult":
        """Expand minimal reasoner extraction into the parser/compiler shape."""
        return QueryExtractionResult(
            intent=self.intent,
            query_operation=self.query_operation,
            filters=self.filters.model_copy(deep=True),
            time_range=self.time_range.model_copy(deep=True),
            comparison=self.comparison.model_copy(deep=True) if self.comparison is not None else None,
            aggregation=self.aggregation.model_copy(deep=True) if self.aggregation is not None else None,
            request_shape=self.request_shape,
            fact_query_kind=self.fact_query_kind,
            result_limit=self.result_limit,
            result_reference=self.result_reference,
            answer_fact_field=self.answer_fact_field,
            raw_query=self.raw_query,
        )


class ResolverOutcome(str, Enum):
    """Outcome of the resolution process."""

    OK = "OK"  # Proceed with query
    NEEDS_INPUT = "NEEDS_INPUT"  # Ask user for clarification
    NEGOTIATED = "NEGOTIATED"  # Clamped or modified, but proceeding


class QueryParseResult(BaseModel):
    """Structured result from the Query Parser."""

    outcome: ResolverOutcome
    extraction: QueryExtractionResult | None = None
    query_ir: dict[str, Any] | None = Field(default=None, description="Compiled query IR snapshot")
    query_contract: dict[str, Any] | None = Field(default=None, description="Compiled execution contract snapshot")
    resolver_message: str | None = Field(default=None, description="Message to show user (e.g. clarification)")
    notices: list[str] = Field(default_factory=list, description="Infos like 'Clamped to 30 days'")
    pending_clarification: dict[str, Any] | None = Field(
        default=None,
        description="Structured unresolved clarification state for multi-turn query follow-ups",
    )
    patch: dict[str, Any] | None = Field(default=None, description="State updates")


class PendingClarificationState(BaseModel):
    """Semantic unresolved query state persisted between clarification turns."""

    kind: Literal["pending_clarification"] = "pending_clarification"
    original_query: str
    current_intent: ExtractionIntent
    original_extraction: QueryExtractionResult
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    resolver_message: str | None = None
    language: str = "en"
