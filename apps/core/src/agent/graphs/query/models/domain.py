"""Pydantic models for query service."""

from datetime import date
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class QueryIntent(str, Enum):
    """Explicit query intent types - each maps to one execution path."""

    TRANSACTION_LIST = "transaction_list"
    TRANSACTION_SEARCH = "transaction_search"
    ANALYTICS_SUMMARY = "analytics_summary"
    TIME_COMPARISON = "time_comparison"
    BENEFICIARY_SUMMARY = "beneficiary_summary"
    AFFORDABILITY = "affordability"


class TimeRange(BaseModel):
    """Time range for queries with optional granularity."""

    start: date
    end: date
    granularity: Literal["day", "week", "month"] | None = None


class Filters(BaseModel):
    """Query filters - all optional, applied additively."""

    category: list[str] | None = Field(default=None, description="Category keywords: food, transport, etc.")
    merchant: list[str] | None = Field(default=None, description="Merchant/narration keywords")
    min_amount: float | None = Field(default=None, description="Minimum amount in naira")
    max_amount: float | None = Field(default=None, description="Maximum amount in naira")
    transaction_type: Literal["credit", "debit"] | None = Field(default=None, description="Filter by type")
    exclude: list[str] | None = Field(default=None, description="Exclude patterns")
    account_filter: str | None = Field(default=None, description="Bank/account name to filter by")


class Aggregation(BaseModel):
    """Aggregation options for analytics queries."""

    type: Literal["sum", "average", "count", "largest", "smallest", "breakdown"] = Field(default="sum")
    group_by: Literal["category", "merchant", "day", "account"] | None = None
    limit: int | None = Field(default=5, ge=1, le=100)
    sort_by: Literal["amount", "count"] | None = Field(
        default="amount", description="Sort by total amount or transaction count"
    )


class NormalizedQuery(BaseModel):
    """
    Legacy-compatible normalized query snapshot used by formatters/session continuity.

    QueryExecutionContract is the runtime source of truth for execution.
    """

    intent: QueryIntent
    time_range: TimeRange | None = None
    filters: Filters | None = None
    aggregation: Aggregation | None = None
    accounts_scope: Literal["single", "all"] = Field(default="all")
    account_name: str | None = Field(default=None, description="Specific account name if user mentions one")
    source_message_id: str | None = None

    amount_check: float | None = Field(default=None, description="Amount for affordability check")
    item_name: str | None = Field(default=None, description="Product name for price lookup")
    analysis_type: Literal["immediate", "relative", "simulated", "remainder"] = "immediate"
    result_limit: int | None = Field(
        default=None, ge=1, le=100, description="Max results to return (e.g., 'last transaction' = 1)"
    )
    result_reference: Literal["latest", "oldest"] | None = Field(
        default=None, description="Relative positioning for results when user asks for most recent/oldest"
    )


class ComparisonDirective(BaseModel):
    """Structured comparison behavior for time-comparison execution."""

    mode: Literal["previous_equivalent", "year_ago", "explicit_range"] = Field(default="previous_equivalent")
    explicit_range: TimeRange | None = None


class QueryIR(BaseModel):
    """LLM-facing interpretation model before runtime contract compilation."""

    intent: QueryIntent
    raw_query: str | None = None
    language: str = "en"
    timezone: str = "Africa/Lagos"
    time_range: TimeRange
    filters: Filters | None = None
    aggregation: Aggregation | None = None
    accounts_scope: Literal["single", "all"] = Field(default="all")
    account_name: str | None = None
    amount_check: float | None = None
    item_name: str | None = None
    analysis_type: Literal["immediate", "relative", "simulated", "remainder"] = "immediate"
    result_limit: int | None = Field(default=None, ge=1, le=100)
    result_reference: Literal["latest", "oldest"] | None = None
    comparison: ComparisonDirective | None = None
    continuation_type: str | None = None
    continuation_delta_type: str | None = None

    def to_normalized_query(self) -> NormalizedQuery:
        """Build legacy-compatible NormalizedQuery view from IR."""
        return NormalizedQuery(
            intent=self.intent,
            time_range=self.time_range,
            filters=self.filters,
            aggregation=self.aggregation,
            accounts_scope=self.accounts_scope,
            account_name=self.account_name,
            amount_check=self.amount_check,
            item_name=self.item_name,
            analysis_type=self.analysis_type,
            result_limit=self.result_limit,
            result_reference=self.result_reference,
        )


class QueryExecutionContract(BaseModel):
    """Runtime-facing contract consumed by query handlers."""

    intent: QueryIntent
    time_start: date
    time_end: date
    timezone: str = "Africa/Lagos"
    filters: Filters | None = None
    aggregation: Aggregation | None = None
    accounts_scope: Literal["single", "all"] = Field(default="all")
    account_name: str | None = None
    amount_check: float | None = None
    item_name: str | None = None
    analysis_type: Literal["immediate", "relative", "simulated", "remainder"] = "immediate"
    result_limit: int | None = Field(default=None, ge=1, le=100)
    result_reference: Literal["latest", "oldest"] | None = None
    comparison: ComparisonDirective | None = None
    continuation_type: str | None = None
    continuation_delta_type: str | None = None
    normalized_query: NormalizedQuery

    @classmethod
    def from_query_ir(cls, ir: QueryIR) -> "QueryExecutionContract":
        """Compile runtime contract from QueryIR."""
        normalized = ir.to_normalized_query()
        return cls(
            intent=ir.intent,
            time_start=ir.time_range.start,
            time_end=ir.time_range.end,
            timezone=ir.timezone,
            filters=ir.filters,
            aggregation=ir.aggregation,
            accounts_scope=ir.accounts_scope,
            account_name=ir.account_name,
            amount_check=ir.amount_check,
            item_name=ir.item_name,
            analysis_type=ir.analysis_type,
            result_limit=ir.result_limit,
            result_reference=ir.result_reference,
            comparison=ir.comparison,
            continuation_type=ir.continuation_type,
            continuation_delta_type=ir.continuation_delta_type,
            normalized_query=normalized,
        )

    @classmethod
    def from_normalized_query(
        cls,
        query: NormalizedQuery,
        *,
        timezone: str = "Africa/Lagos",
        comparison: ComparisonDirective | None = None,
        continuation_type: str | None = None,
        continuation_delta_type: str | None = None,
    ) -> "QueryExecutionContract":
        """Build runtime contract from legacy NormalizedQuery."""
        time_range = query.time_range
        if time_range is None:
            raise ValueError("NormalizedQuery.time_range is required for QueryExecutionContract")

        ir = QueryIR(
            intent=query.intent,
            timezone=timezone,
            time_range=time_range,
            filters=query.filters,
            aggregation=query.aggregation,
            accounts_scope=query.accounts_scope,
            account_name=query.account_name,
            amount_check=query.amount_check,
            item_name=query.item_name,
            analysis_type=query.analysis_type,
            result_limit=query.result_limit,
            result_reference=query.result_reference,
            comparison=comparison,
            continuation_type=continuation_type,
            continuation_delta_type=continuation_delta_type,
        )
        contract = cls.from_query_ir(ir)
        # Preserve the exact input query snapshot for formatter/session compatibility.
        contract.normalized_query = query
        return contract


class SurfaceType(str, Enum):
    """Type of result surface presented to the user."""

    LIST = "list"
    BREAKDOWN = "breakdown"
    SUMMARY = "summary"
    SINGLE_ITEM = "single_item"


class ResultSurface(BaseModel):
    """
    Describes the current 'view' or 'surface' the user is looking at.
    Used for deterministic continuation and drill-down.
    """

    type: SurfaceType
    items: list[dict[str, Any]] = Field(default_factory=list, description="Simplified items context (id, key, amount)")
    context: dict[str, Any] = Field(default_factory=dict, description="Context metadata (group_by, time_range, etc)")


class QueryResultItem(BaseModel):
    """Single item in query results - quotable and drill-down capable."""

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    description: str
    amount: float
    date: date
    metadata: dict[str, Any] | None = None


class QueryResult(BaseModel):
    """
    Query execution result - includes context_key for follow-ups.

    The context_key is saved to Redis for conversational continuity.
    """

    summary_text: str
    items: list[QueryResultItem] | None = None
    context_key: str = Field(default_factory=lambda: f"qr:{uuid4()}")
    has_more: bool = False
    query_snapshot: NormalizedQuery | None = None  # For follow-up deltas
    query_contract: QueryExecutionContract | None = None
    surface: ResultSurface | None = None  # UI/Interaction surface state
    cached_transactions: list[dict[str, Any]] | None = None
    cache_fetched_at: float | None = None
    cache_fingerprint: str | None = None
    cache_reused: bool = False


CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "food": ["restaurant", "food", "chicken", "pizza", "chowdeck", "jumia food", "mr biggs", "kfc", "dominos"],
    "transport": ["uber", "bolt", "taxi", "ride", "trip", "fuel", "petrol", "nnpc"],
    "entertainment": ["netflix", "spotify", "youtube", "dstv", "showmax", "prime video"],
    "utilities": ["ikedc", "ekedc", "nepa", "electricity", "water", "phcn", "lawma"],
    "airtime": ["airtime", "recharge", "mtn", "glo", "airtel", "9mobile", "data bundle"],
    "transfers": ["transfer", "nip", "payment to", "payment from"],
    "bank_charges": ["sms alert", "maintenance", "stamp duty", "vat", "card", "atm"],
    "shopping": ["jumia", "konga", "shoprite", "pos purchase", "slot", "spar"],
    "savings": ["piggyvest", "cowrywise", "savings", "investment"],
}


def match_category(narration: str, categories: list[str]) -> bool:
    """Check if narration matches any of the specified categories."""
    narration_lower = narration.lower()
    for cat in categories:
        cat_key = cat.lower().strip()
        if cat_key in CATEGORY_KEYWORDS:
            for keyword in CATEGORY_KEYWORDS[cat_key]:
                if keyword in narration_lower:
                    return True
        elif cat_key in narration_lower:
            return True
    return False


def detect_category(narration: str) -> str | None:
    """Detect category from narration."""
    narration_lower = narration.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in narration_lower:
                return category
    return None
