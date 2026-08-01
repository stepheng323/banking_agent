"""Checkpoint-safe contracts for self-healing query conversations.

The query executor continues to execute a canonical ``QueryRequest``.  These
models own the conversational layer around it: salience, bounded multi-step
read plans, and grounded interpretation proposals.  They intentionally never
store raw user text or hidden result rows.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.operations import QueryRequest


class ConversationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


FocusSource = Literal["user_query", "user_refinement", "user_selection", "assistant_evidence"]
FocusSubject = Literal["transactions", "summary", "comparison", "insight", "affordability"]
ScopeMutation = Literal["replace", "add", "remove", "clear", "all"]


class QueryFocus(ConversationModel):
    """Semantic focus, separate from the currently rendered surface."""

    frame_id: str | None = None
    step_id: str | None = None
    subject: FocusSubject = "transactions"
    measure: str | None = None
    statistic: str | None = None
    dimension: str | None = None
    account_scope: str | None = None
    selected_payload: SelectionPayload | None = None
    source: FocusSource = "user_query"
    originating_turn_id: str | None = None
    latest_user_turn_id: str | None = None


class SingleQueryExecution(ConversationModel):
    kind: Literal["single"] = "single"
    request: QueryRequest


class QueryPlanBinding(ConversationModel):
    """A bounded structured value flow between two read-only plan steps."""

    source_step_id: str
    source: Literal["top_group", "selected_group", "scalar", "period"]
    target: Literal["category", "counterparty", "account", "period", "amount"]


class QueryPlanStep(ConversationModel):
    step_id: str = Field(min_length=1, max_length=24)
    request: QueryRequest
    role: Literal["primary", "supporting", "evidence"] = "primary"
    depends_on: list[str] = Field(default_factory=list, max_length=2)
    required: bool = True
    bindings: list[QueryPlanBinding] = Field(default_factory=list, max_length=2)


class QueryTurnPlan(ConversationModel):
    """At most three ordered read-only queries for one user turn."""

    kind: Literal["plan"] = "plan"
    steps: list[QueryPlanStep] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def validate_steps(self) -> QueryTurnPlan:
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("query plan step ids must be unique")
        positions = {step_id: index for index, step_id in enumerate(ids)}
        if sum(step.role == "primary" for step in self.steps) != 1:
            raise ValueError("query plan must contain exactly one primary step")
        for index, step in enumerate(self.steps):
            if any(dependency not in positions or positions[dependency] >= index for dependency in step.depends_on):
                raise ValueError("query plan dependencies must reference earlier steps")
            for binding in step.bindings:
                if binding.source_step_id not in step.depends_on:
                    raise ValueError("query plan binding source must be declared as a dependency")
                if binding.source_step_id not in positions or positions[binding.source_step_id] >= index:
                    raise ValueError("query plan bindings must reference earlier steps")
        return self


QueryExecutionContract = Annotated[SingleQueryExecution | QueryTurnPlan, Field(discriminator="kind")]


class QueryScopeDelta(ConversationModel):
    """Sparse, typed edits to a focused query contract.

    Omitted fields preserve the source contract.  Values are only executable
    after the delta applier validates them for the source operation.
    """

    period_mutation: ScopeMutation | None = None
    period: dict[str, str] | None = None
    account_mutation: ScopeMutation | None = None
    account_names: list[str] = Field(default_factory=list, max_length=5)
    counterparty_mutation: ScopeMutation | None = None
    counterparty: str | None = None
    direction_mutation: ScopeMutation | None = None
    direction: Literal["credit", "debit"] | None = None
    category_mutation: ScopeMutation | None = None
    categories: list[str] = Field(default_factory=list, max_length=10)
    status_mutation: ScopeMutation | None = None
    statuses: list[Literal["failed", "pending", "successful", "reversed"]] = Field(default_factory=list, max_length=4)
    event_type_mutation: ScopeMutation | None = None
    event_types: list[str] = Field(default_factory=list, max_length=10)
    exclusion_mutation: ScopeMutation | None = None
    exclusions: list[str] = Field(default_factory=list, max_length=10)
    amount_mutation: ScopeMutation | None = None
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    measure: (
        Literal[
            "spending",
            "income",
            "net_cash_flow",
            "transactions",
            "cash_flow_overview",
            "inflow",
            "outflow",
        ]
        | None
    ) = None
    statistic: Literal["sum", "count", "average", "largest", "smallest"] | None = None
    dimension: (
        Literal[
            "category",
            "counterparty",
            "day",
            "account",
            "transaction_type",
            "event_type",
            "cash_flow_class",
        ]
        | None
    ) = None
    rank: Literal["amount", "count"] | None = None
    cardinality: Literal["one", "many"] | None = None
    analysis_basis: Literal["ledger_transactions", "economic_events"] | None = None
    confidence_policy: Literal["include", "exclude_uncertain", "segment_uncertain"] | None = None
    completeness_policy: Literal["disclose", "require_complete"] | None = None

    @model_validator(mode="after")
    def validate_amount_bounds(self) -> QueryScopeDelta:
        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            raise ValueError("minimum amount cannot exceed maximum amount")
        return self


class QueryInterpretationProposal(ConversationModel):
    proposal_id: str = Field(min_length=1, max_length=24)
    contract: QueryExecutionContract
    source_frame_id: str | None = None
    difference_fields: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)


class QueryInputCandidate(ConversationModel):
    """A visible, bounded choice preserved without retaining a result row."""

    label: str = Field(min_length=1, max_length=240)
    payload: SelectionPayload
    frame_id: str | None = None


class PendingFieldClarification(ConversationModel):
    kind: Literal["field_clarification"] = "field_clarification"
    source_frame_id: str | None = None
    original_query: str = ""
    clarification_type: Literal[
        "time",
        "selection",
        "recipient",
        "account",
        "direction",
        "category",
        "status",
        "amount",
        "scope",
    ] | None = None
    target_field: str | None = None
    candidate_payloads: list[QueryInputCandidate] = Field(default_factory=list, max_length=5)
    original_operation: dict[str, str | None] = Field(default_factory=dict)
    query_request: QueryRequest | None = None
    original_extraction: dict[str, object] | None = None
    ambiguities: list[dict[str, object]] = Field(default_factory=list, max_length=10)
    resolver_message: str | None = None
    language: str = "en"
    attempt_count: int = Field(default=0, ge=0, le=2)
    created_turn_id: str | None = None


class PendingInterpretationProposal(ConversationModel):
    kind: Literal["interpretation_proposal"] = "interpretation_proposal"
    source_frame_id: str | None = None
    proposals: list[QueryInterpretationProposal] = Field(min_length=2, max_length=2)
    attempt_count: int = Field(default=0, ge=0, le=2)
    created_turn_id: str | None = None


PendingQueryInput = Annotated[
    PendingFieldClarification | PendingInterpretationProposal,
    Field(discriminator="kind"),
]


class QuerySessionV3(ConversationModel):
    """The sole persisted authority for a query conversation."""

    schema_version: Literal[3] = 3
    session_active: bool = True
    execution_contract: QueryExecutionContract | None = None
    active_focus: QueryFocus | None = None
    display_frame_id: str | None = None
    display_result: dict[str, object] | None = None
    query_frames: list[dict] = Field(default_factory=list, max_length=3)
    pending_input: PendingQueryInput | None = None
    current_page: int = Field(default=0, ge=0)
    page_size: int = Field(default=5, ge=1, le=20)
    show_expanded: bool = False
    timestamp: float | None = None
    recent_read_only: bool = False
    cache: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "FocusSource",
    "PendingFieldClarification",
    "PendingInterpretationProposal",
    "PendingQueryInput",
    "QueryExecutionContract",
    "QueryFocus",
    "QueryInputCandidate",
    "QueryInterpretationProposal",
    "QueryPlanBinding",
    "QueryPlanStep",
    "QueryScopeDelta",
    "QuerySessionV3",
    "QueryTurnPlan",
    "SingleQueryExecution",
]
