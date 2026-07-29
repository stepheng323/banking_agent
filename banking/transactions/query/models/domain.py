"""Pydantic models for query service."""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from banking.transactions.query.contracts import FocusedReferent, InsightEvidenceSelection, SurfaceView, SurfaceViewMode
from banking.transactions.query.models.conversation import QueryExecutionContract, QueryFocus
from banking.transactions.query.models.operations import QueryRequest


class QueryIntent(str, Enum):
    """Explicit query intent types - each maps to one execution path."""

    TRANSACTION_LIST = "transaction_list"
    TRANSACTION_SEARCH = "transaction_search"
    TRANSACTION_DETAIL = "transaction_detail"
    ANALYTICS_SUMMARY = "analytics_summary"
    TIME_COMPARISON = "time_comparison"
    CASH_FLOW_SUMMARY = "cash_flow_summary"
    BENEFICIARY_SUMMARY = "beneficiary_summary"
    AFFORDABILITY = "affordability"
    INSIGHT = "insight"
    QUERY_CLARIFICATION = "query_clarification"


QueryFactField = Literal[
    "date",
    "counterparty",
    "amount",
    "bank",
    "status",
    "description",
    "reference",
    "account",
    "direction",
    "category",
]

QueryContractRequestShape = Literal[
    "fact",
    "existence",
    "detail",
    "list",
    "grouped_summary",
    "analytics",
    "comparison",
    "affordability",
    "insight",
]


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
    min_amount_inclusive: bool = Field(default=True, description="Whether the minimum amount is inclusive")
    max_amount_inclusive: bool = Field(default=True, description="Whether the maximum amount is inclusive")
    transaction_type: Literal["credit", "debit"] | None = Field(default=None, description="Filter by type")
    status: Literal["failed", "pending", "successful", "reversed"] | None = Field(
        default=None,
        description="Filter by transaction status",
    )
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


class QueryObjective(str, Enum):
    """Semantic objective of a query before execution."""

    FACT = "fact"
    TRANSACTION_LIST = "transaction_list"
    GROUPED_SUMMARY = "grouped_summary"
    COMPARISON = "comparison"
    AFFORDABILITY = "affordability"
    ACTION_HANDOFF = "action_handoff"
    INSIGHT = "insight"


class QuerySubject(str, Enum):
    """Primary subject a query is asking about."""

    TRANSACTIONS = "transactions"
    BENEFICIARIES = "beneficiaries"
    ACCOUNTS = "accounts"


class UserRequestShape(str, Enum):
    """Requested answer shape before presentation planning."""

    DIRECT_ANSWER = "direct_answer"
    SUMMARY = "summary"
    LIST = "list"
    CLARIFY = "clarify"


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


class QueryAnswerStrategy(str, Enum):
    """Explicit rendering strategy chosen after execution."""

    DIRECT_ANSWER = "direct_answer"
    SUMMARY_LIST = "summary_list"
    TRANSACTION_LIST = "transaction_list"
    CLARIFY = "clarify"
    INSIGHT = "insight"


class QueryAnswerContext(BaseModel):
    """Structured answer payload for direct-answer and clarify views."""

    primary_text: str
    secondary_text: str | None = None
    hint_text: str | None = None


class QueryFrame(BaseModel):
    """Compact session memory for recent query results."""

    frame_id: str
    turn_index: int = Field(ge=1)
    query_request: QueryRequest
    execution_contract: QueryExecutionContract | None = None
    focus: QueryFocus | None = None
    source_frame_id: str | None = None
    summary_text: str
    interpretation: dict[str, Any] | None = None
    surface_type: SurfaceViewMode | None = None
    surface_context: dict[str, Any] = Field(default_factory=dict)
    visible_items: list[dict[str, Any]] = Field(default_factory=list)
    facts: QueryFrameFacts = Field(default_factory=QueryFrameFacts)


class QueryResultItem(BaseModel):
    """Single item in query results - quotable and drill-down capable."""

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    description: str
    amount: float
    date: date
    metadata: dict[str, Any] | None = None


class AccountCashFlowBreakdown(BaseModel):
    """Per-account breakdown for cash flow queries."""

    account_id: str
    bank_name: str
    masked_account_number: str
    total_inflow: int
    total_outflow: int
    net_flow: int


class CashFlowSummaryResult(BaseModel):
    """Rich payload for cash flow summary queries."""

    period_label: str
    currency: str = "NGN"
    total_inflow: int
    total_outflow: int
    net_flow: int
    inflow_count: int
    outflow_count: int
    account_scope: Literal["single", "all"]
    account_breakdown: list[AccountCashFlowBreakdown] | None = None
    excluded_internal_transfers_count: int = 0
    excluded_reversals_count: int = 0
    status: Literal["positive", "negative", "neutral"]


class QueryResult(BaseModel):
    """
    Query execution result - includes context_key for follow-ups.

    The context_key is saved to Redis for conversational continuity.
    """

    summary_text: str
    items: list[QueryResultItem] | None = None
    context_key: str = Field(default_factory=lambda: f"qr:{uuid4()}")
    has_more: bool = False
    conversational_prefix: str | None = None
    query_request: QueryRequest | None = None
    interpretation: dict[str, Any] | None = None
    surface_view: SurfaceView | None = None
    answer_strategy: QueryAnswerStrategy | None = None
    answer_context: QueryAnswerContext | None = None
    cash_flow: CashFlowSummaryResult | None = None
    followup_referent: FocusedReferent | None = None
    conversation_focus: QueryFocus | None = None
    cached_transactions: list[dict[str, Any]] | None = None
    cache_fetched_at: float | None = None
    cache_fingerprint: str | None = None
    cache_scope_fingerprint: str | None = None
    cache_window_start: str | None = None
    cache_window_end: str | None = None
    cache_reused: bool = False


class QuerySectionResult(BaseModel):
    """One executed query-plan step retained for deterministic composition."""

    step_id: str
    role: Literal["primary", "supporting", "evidence"]
    result: QueryResult | None = None
    unavailable_reason: str | None = None


class QueryTurnResult(BaseModel):
    """Canonical response payload for one single or composite query turn."""

    execution_contract: QueryExecutionContract
    summary_text: str
    sections: list[QuerySectionResult] = Field(default_factory=list, max_length=3)
    surface_view: SurfaceView | None = None
    has_more: bool = False


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
    from banking.transactions.query.services.analysis.narration import analyze_transaction_narration

    return analyze_transaction_narration(narration=narration, transaction_type=None).resolved_category


def resolve_transaction_category(category: str | None, narration: str) -> tuple[str | None, str | None]:
    """Resolve the best available category and where it came from."""
    from banking.transactions.query.services.analysis.narration import analyze_transaction_narration

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
    return normalize_category(transaction.get("category"))


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


class InsightResultEnvelope(BaseModel):
    """Common metadata returned with any insight result."""

    coverage: float
    analyzed_period_start: date | None = None
    analyzed_period_end: date | None = None
    history_used_days: int
    excluded_value: float = 0.0
    uncertain_value: float = 0.0
    minimum_data_status: Literal["met", "insufficient_history", "partial_coverage"] = "met"
    evidence_selectors: list[InsightEvidenceSelection] = Field(default_factory=list)
