"""Query extraction models for parser and reasoner outputs."""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.domain import QueryFactField, QueryIntent

# Schema version for future-proofing
SCHEMA_VERSION = 1


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
    INSIGHT = "insight"


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
    min_amount_inclusive: bool = Field(default=True)
    max_amount_inclusive: bool = Field(default=True)
    category: str | None = Field(default=None)
    transaction_type: str | None = Field(default=None, description="credit/debit")
    status: Literal["failed", "pending", "successful", "reversed"] | None = Field(default=None)
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
    """Comparison meaning extracted from the user's wording."""

    mode: Literal["previous_equivalent", "year_ago", "explicit_period"] = Field(default="previous_equivalent")
    period: str | None = Field(default=None, description="Explicit comparison period when mode=explicit_period")


class InsightSpec(BaseModel):
    """Compact provider-facing insight extraction.

    Runtime insight operations remain discriminated models.  The LLM-facing
    contract is intentionally flat so OpenAI strict structured output does not
    receive nested ``oneOf`` schemas from analytical evidence selectors.
    Evidence is attached deterministically by continuation handling, never
    extracted from a fresh user utterance.
    """

    insight_type: Literal[
        "variance_drivers",
        "probable_duplicates",
        "recurring_patterns",
        "anomalies",
        "counterparty_concentration",
        "forecast",
        "runway",
        "cash_flow_quality",
    ]
    analysis_basis: Literal["ledger_transactions", "economic_events"] = "economic_events"
    confidence_policy: Literal["include", "exclude_uncertain", "segment_uncertain"] = "segment_uncertain"
    completeness_policy: Literal["disclose", "require_complete"] = "disclose"
    evidence_limit: int = Field(default=5, ge=1, le=20)
    measure: Literal["spending", "income", "inflow", "outflow", "net_cash_flow", "cash_flow_overview"] | None = None
    dimensions: list[Literal["category", "counterparty", "account", "event_type", "cash_flow_class"]] = Field(
        default_factory=list
    )
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    baseline_days: int | None = Field(default=None, ge=1, le=365)
    min_comparable_observations: int | None = Field(default=None, ge=1)
    min_covered_days: int | None = Field(default=None, ge=1)
    horizon_days: int | None = Field(default=None, ge=7, le=90)
    history_days: int | None = Field(default=None, ge=1, le=730)
    min_complete_months: int | None = Field(default=None, ge=1, le=24)


class ClarificationPatch(BaseModel):
    """Patch payload for query clarification follow-ups."""

    target_session_id: str | None = Field(default=None)
    fields: dict[str, Any] = Field(default_factory=dict)
    time_range: QueryTimeRange | None = None
    recipient: str | None = None
    account_filter: str | None = None
    transaction_type: Literal["credit", "debit"] | None = None
    category: str | None = None
    status: Literal["failed", "pending", "successful", "reversed"] | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    selected_payload: SelectionPayload | None = None
    confidence: float = Field(default=1.0)


class QueryStepExtraction(BaseModel):
    """One compact extraction used by a bounded read-only query plan."""

    intent: QueryIntent = Field(default=QueryIntent.TRANSACTION_LIST)
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
    insight: InsightSpec | None = Field(default=None)


class QueryPlanBindingDraft(BaseModel):
    source_step_id: str
    source: Literal["top_group", "selected_group", "scalar", "period"]
    target: Literal["category", "counterparty", "account", "period", "amount"]


class QueryPlanStepDraft(BaseModel):
    step_id: str = Field(min_length=1, max_length=24)
    role: Literal["primary", "supporting", "evidence"]
    extraction: QueryStepExtraction
    depends_on: list[str] = Field(default_factory=list, max_length=2)
    required: bool = True
    bindings: list[QueryPlanBindingDraft] = Field(default_factory=list, max_length=2)


class QueryPlanDraft(BaseModel):
    steps: list[QueryPlanStepDraft] = Field(min_length=2, max_length=3)


class ParserQueryExtraction(QueryStepExtraction):
    """Minimal parser-only extraction returned by the fresh-query LLM path."""

    plan: QueryPlanDraft | None = None


class QueryExtractionResult(BaseModel):
    """Pure query extraction with requested_capabilities."""

    schema_version: int = Field(default=SCHEMA_VERSION)
    intent: QueryIntent = Field(default=QueryIntent.TRANSACTION_LIST)
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
    insight: InsightSpec | None = Field(default=None)
    clarification_patch: ClarificationPatch | None = Field(default=None)

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

    intent: QueryIntent = Field(default=QueryIntent.TRANSACTION_LIST)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    time_range: QueryTimeRange = Field(default_factory=QueryTimeRange)
    comparison: QueryComparison | None = Field(default=None)
    aggregation: QueryAggregation | None = Field(default=None)
    request_shape: QueryRequestShape | None = Field(default=None)
    fact_query_kind: FactQueryKind | None = Field(default=None)
    result_limit: int | None = Field(default=None, ge=1, le=100)
    result_reference: Literal["latest", "oldest"] | None = Field(default=None)
    answer_fact_field: QueryFactField | None = Field(default=None)
    insight: InsightSpec | None = Field(default=None)
    raw_query: str | None = Field(default=None)

    def to_query_extraction_result(self) -> "QueryExtractionResult":
        """Expand minimal reasoner extraction into the parser/compiler shape."""
        return QueryExtractionResult(
            intent=self.intent,
            filters=self.filters.model_copy(deep=True),
            time_range=self.time_range.model_copy(deep=True),
            comparison=self.comparison.model_copy(deep=True) if self.comparison is not None else None,
            aggregation=self.aggregation.model_copy(deep=True) if self.aggregation is not None else None,
            request_shape=self.request_shape,
            fact_query_kind=self.fact_query_kind,
            result_limit=self.result_limit,
            result_reference=self.result_reference,
            answer_fact_field=self.answer_fact_field,
            insight=self.insight.model_copy(deep=True) if self.insight is not None else None,
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
    query_request: dict[str, Any] | None = Field(default=None, description="Compiled Query Semantics v2 request")
    execution_contract: dict[str, Any] | None = None
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
    current_intent: QueryIntent
    original_extraction: QueryExtractionResult | None = None
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    resolver_message: str | None = None
    language: str = "en"
    clarification_type: (
        Literal["time", "selection", "recipient", "account", "direction", "category", "status", "amount", "scope"]
        | None
    ) = None
    target_field: str | None = None
    candidate_payloads: list["ClarificationCandidate"] = Field(default_factory=list, max_length=5)
    original_operation: "ClarificationOperation | None" = None
    query_request: dict[str, Any] | None = None
    attempt_count: int = Field(default=0, ge=0, le=2)
    created_turn_id: str | None = None


class ClarificationCandidate(BaseModel):
    """Bounded selectable candidate retained across clarification turns."""

    label: str
    payload: SelectionPayload
    frame_id: str | None = None


class ClarificationOperation(BaseModel):
    """Read-only query operation to restore after clarification."""

    continuation_type: str | None = None
    drill_down_action: str | None = None
    fact_field: str | None = None
    grounded_operation: str | None = None
