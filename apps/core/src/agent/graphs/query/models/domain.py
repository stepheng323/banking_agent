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

    type: Literal["sum", "average", "count", "largest", "breakdown"] = Field(default="sum")
    group_by: Literal["category", "merchant", "day", "account"] | None = None
    limit: int | None = Field(default=10, ge=1, le=100)
    sort_by: Literal["amount", "count"] | None = Field(default="amount", description="Sort by total amount or transaction count")


class NormalizedQuery(BaseModel):
    """
    Canonical query structure - all LLM output compiles into this.

    This is the single source of truth for query execution.
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
    result_limit: int | None = Field(default=None, ge=1, le=100, description="Max results to return (e.g., 'last transaction' = 1)")


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
        if cat in CATEGORY_KEYWORDS:
            for keyword in CATEGORY_KEYWORDS[cat]:
                if keyword in narration_lower:
                    return True
        elif cat.lower() in narration_lower:
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
