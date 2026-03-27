"""Pydantic models for query service."""

import re
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


class QueryOperation(str, Enum):
    """Bounded internal query operations above handler-level intents."""

    LIST_TRANSACTIONS = "list_transactions"
    SEARCH_SINGLE_TRANSACTION = "search_single_transaction"
    SUM_TRANSACTIONS = "sum_transactions"
    COUNT_TRANSACTIONS = "count_transactions"
    AVERAGE_TRANSACTIONS = "average_transactions"
    RANK_LARGEST_TRANSACTION = "rank_largest_transaction"
    RANK_SMALLEST_TRANSACTION = "rank_smallest_transaction"
    BREAKDOWN_TRANSACTIONS = "breakdown_transactions"
    COMPARE_PERIODS = "compare_periods"
    SUMMARIZE_BENEFICIARIES = "summarize_beneficiaries"
    CHECK_AFFORDABILITY = "check_affordability"


class TimeRange(BaseModel):
    """Time range for queries with optional granularity."""

    start: date
    end: date
    granularity: Literal["day", "week", "month"] | None = None


class Filters(BaseModel):
    """Query filters - all optional, applied additively."""

    category: list[str] | None = Field(default=None, description="Category keywords: food, transport, etc.")
    merchant: list[str] | None = Field(default=None, description="Merchant/narration keywords")
    counterparty: list[str] | None = Field(default=None, description="Parsed sender/recipient/merchant match")
    min_amount: float | None = Field(default=None, description="Minimum amount in naira")
    max_amount: float | None = Field(default=None, description="Maximum amount in naira")
    transaction_type: Literal["credit", "debit"] | None = Field(default=None, description="Filter by type")
    exclude: list[str] | None = Field(default=None, description="Exclude patterns")
    account_filter: str | None = Field(default=None, description="Bank/account name to filter by")


class Aggregation(BaseModel):
    """Aggregation options for analytics queries."""

    type: Literal["sum", "average", "count", "largest", "smallest", "breakdown"] = Field(default="sum")
    group_by: Literal["category", "merchant", "day", "account", "transaction_type"] | None = None
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
    query_operation: QueryOperation | None = None
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
    answer_fact_field: Literal["date", "counterparty", "amount", "bank"] | None = Field(
        default=None,
        description="Fact to answer directly when a single matching transaction is found",
    )


class ComparisonDirective(BaseModel):
    """Structured comparison behavior for time-comparison execution."""

    mode: Literal["previous_equivalent", "year_ago", "explicit_range"] = Field(default="previous_equivalent")
    explicit_range: TimeRange | None = None


class QueryIR(BaseModel):
    """LLM-facing interpretation model before runtime contract compilation."""

    intent: QueryIntent
    query_operation: QueryOperation | None = None
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
    answer_fact_field: Literal["date", "counterparty", "amount", "bank"] | None = None
    comparison: ComparisonDirective | None = None
    continuation_type: str | None = None
    continuation_delta_type: str | None = None

    def to_normalized_query(self) -> NormalizedQuery:
        """Build legacy-compatible NormalizedQuery view from IR."""
        return NormalizedQuery(
            intent=self.intent,
            query_operation=self.query_operation,
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
            answer_fact_field=self.answer_fact_field,
        )


class QueryExecutionContract(BaseModel):
    """Runtime-facing contract consumed by query handlers."""

    intent: QueryIntent
    query_operation: QueryOperation | None = None
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
    answer_fact_field: Literal["date", "counterparty", "amount", "bank"] | None = None
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
            query_operation=ir.query_operation,
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
            answer_fact_field=ir.answer_fact_field,
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
            query_operation=query.query_operation,
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
            answer_fact_field=query.answer_fact_field,
            comparison=comparison,
            continuation_type=continuation_type,
            continuation_delta_type=continuation_delta_type,
        )
        contract = cls.from_query_ir(ir)
        # Preserve the exact input query snapshot for formatter/session compatibility.
        contract.normalized_query = query
        return contract


class QueryFrameFacts(BaseModel):
    """Compact derived facts for conversational follow-ups over prior query results."""

    metric_kind: Literal[
        "amount",
        "count",
        "average",
        "ranked",
        "transactions",
        "comparison",
        "single_item",
        "unknown",
    ] = "unknown"
    amount: float | None = None
    comparison_amount: float | None = None
    count: int | None = None
    direction: Literal["credit", "debit"] | None = None
    label: str | None = None


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


class QueryFrame(BaseModel):
    """Compact session memory for recent query results."""

    frame_id: str
    turn_index: int = Field(ge=1)
    query_contract: QueryExecutionContract
    summary_text: str
    interpretation: dict[str, Any] | None = None
    surface_type: SurfaceType | None = None
    surface_context: dict[str, Any] = Field(default_factory=dict)
    facts: QueryFrameFacts = Field(default_factory=QueryFrameFacts)


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
    interpretation: dict[str, Any] | None = None
    surface: ResultSurface | None = None  # UI/Interaction surface state
    cached_transactions: list[dict[str, Any]] | None = None
    cache_fetched_at: float | None = None
    cache_fingerprint: str | None = None
    cache_scope_fingerprint: str | None = None
    cache_window_start: str | None = None
    cache_window_end: str | None = None
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

CATEGORY_ALIASES: dict[str, str] = {
    "bank_charge": "bank_charges",
    "bank_charges": "bank_charges",
    "bank_fees": "bank_charges",
    "bills": "utilities",
    "bills_utilities": "utilities",
    "cash_withdrawal": "cash_withdrawal",
    "data": "airtime",
    "electronics": "shopping",
    "entertainment": "entertainment",
    "fees": "bank_charges",
    "food": "food",
    "food_and_drink": "food",
    "food_drink": "food",
    "groceries": "food",
    "income": "income",
    "interest": "income",
    "interest_received": "income",
    "investment": "savings",
    "investment_deposit": "savings",
    "investment_payout": "income",
    "investments": "savings",
    "mobile": "airtime",
    "mobile_data": "airtime",
    "online_payments": "shopping",
    "other_incoming_payments": "income",
    "other_incoming_payments_from_employer": "income",
    "other_outgoing_payments": "shopping",
    "personal_transfer": "transfers",
    "salary": "income",
    "saving": "savings",
    "savings": "savings",
    "shopping": "shopping",
    "subscriptions": "entertainment",
    "telecom": "airtime",
    "top_up": "airtime",
    "transfer": "transfers",
    "transfers": "transfers",
    "transport": "transport",
    "transportation": "transport",
    "utilities": "utilities",
    "utility_services": "utilities",
}


def normalize_category(category: str | None) -> str | None:
    """Normalize provider and user category labels to stable internal values."""
    if not category:
        return None

    normalized = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    if not normalized:
        return None
    if normalized in CATEGORY_KEYWORDS:
        return normalized
    return CATEGORY_ALIASES.get(normalized, normalized)


def match_category(narration: str, categories: list[str]) -> bool:
    """Check if narration matches any of the specified categories."""
    narration_lower = narration.lower()
    for cat in categories:
        cat_key = normalize_category(cat) or cat.lower().strip()
        if cat_key in CATEGORY_KEYWORDS:
            for keyword in CATEGORY_KEYWORDS[cat_key]:
                if keyword in narration_lower:
                    return True
        elif cat_key in narration_lower:
            return True
    return False


def detect_category(narration: str) -> str | None:
    """Detect category from narration."""
    from apps.core.src.agent.graphs.query.services.narration import analyze_transaction_narration

    return analyze_transaction_narration(narration=narration, transaction_type=None).resolved_category


def resolve_transaction_category(category: str | None, narration: str) -> tuple[str | None, str | None]:
    """Resolve the best available category and where it came from."""
    from apps.core.src.agent.graphs.query.services.narration import analyze_transaction_narration

    analysis = analyze_transaction_narration(
        narration=narration,
        transaction_type=None,
        provider_category=category,
    )
    return analysis.resolved_category, analysis.category_source


def get_transaction_category(transaction: dict[str, Any]) -> str | None:
    """Return a transaction category, preferring resolved stored values."""
    resolved = normalize_category(transaction.get("resolved_category"))
    if resolved:
        return resolved

    category, _source = resolve_transaction_category(
        transaction.get("category"),
        transaction.get("narration", ""),
    )
    return category


def match_transaction_category(transaction: dict[str, Any], categories: list[str]) -> bool:
    """Check whether a transaction matches any requested category."""
    transaction_category = get_transaction_category(transaction)
    normalized_targets = {normalize_category(category) for category in categories}
    normalized_targets.discard(None)

    if transaction_category and transaction_category in normalized_targets:
        return True

    raw_category = str(transaction.get("category") or "").lower()
    for category in categories:
        normalized = normalize_category(category)
        if normalized and normalized == normalize_category(raw_category):
            return True
        if category.lower().strip() in raw_category:
            return True

    return match_category(transaction.get("narration", ""), categories)
