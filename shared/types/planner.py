"""Models for task planning and normalization."""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContextReference(BaseModel):
    """Pointer to a context entity."""

    selector: Literal["previous", "index", "label"]
    index: int | None = None
    label: str | None = None


class RecipientAllocation(BaseModel):
    """Recipient-side transfer allocation."""

    recipient_name: str = Field(..., description="Recipient/beneficiary name exactly as referenced by the user")
    amount: float = Field(..., gt=0, description="Allocated amount for this recipient")


class FundingSplitUpdate(BaseModel):
    """Source-side funding split for a pending transfer confirmation edit."""

    model_config = ConfigDict(extra="forbid")

    bank_name: str = Field(..., description="User source bank/account reference for this funding leg")
    amount: float = Field(..., gt=0, description="Amount to fund from this source account")


class TaskParameters(BaseModel):
    """Common parameters for tasks."""

    amount: str | float | None = None
    transfer_all: bool = False
    transfer_percentage: float | None = None
    recipient: str | None = None
    recipient_name: str | None = None
    narration: str | None = None
    recipient_phone: str | None = None
    network: str | None = None
    recipient_account: str | None = None
    bank_name: str | None = None
    phone: str | None = None
    budget: str | None = None
    plan: str | None = None
    is_self: bool = False

    schedule: str | None = None
    scheduled: str | None = None
    recurring: bool | None = None
    schedule_id: str | None = None
    schedule_selector: str | None = None
    international: bool | None = None
    alias: str | None = None
    reference: ContextReference | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None
    use_dual_accounts: bool | None = None
    source_accounts: list[str] | None = None
    explicit_split: dict[str, float] | None = None
    recipient_allocations: list[RecipientAllocation] | None = None
    recipient_binding_source: Literal["fanout"] | None = None
    recipient_binding_index: int | None = None


class PlannedTask(BaseModel):
    """Structured representation of a single planned task."""

    task_id: str = Field(..., description="Stable ID referenced by depends_on")
    action: str
    executor: Literal[
        "transfer",
        "query",
        "airtime",
        "data",
        "account",
        "support",
        "faq",
        "beneficiary",
        "orchestrator",
    ]
    instruction: str
    description: str | None = None
    parameters: TaskParameters = Field(default_factory=TaskParameters)
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    risk: Literal["READ_ONLY", "MUTATION", "MONEY_MOVE"] = "READ_ONLY"
    idempotency_key: str | None = None  # Set by engine
    source_clause_index: int | None = Field(
        default=None,
        description="1-based clause index in planner decomposition that produced this task",
    )


PlannerClauseIntentFamily: TypeAlias = Literal[
    "transfer",
    "airtime",
    "data",
    "account_query",
    "query",
    "support",
    "faq",
    "beneficiary",
    "conversational",
    "unknown",
]


class PlannerClause(BaseModel):
    """Ordered clause decomposition for a single user turn."""

    clause_index: int = Field(..., ge=1, description="1-based ordered semantic clause index")
    text: str = Field(default="", description="User clause text for this semantic segment")
    intent_family: PlannerClauseIntentFamily = Field(
        default="unknown",
        description="Normalized semantic family for this clause",
    )
    extracted_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Clause-local extracted fields used for downstream validation and repair",
    )
    task_ids: list[str] = Field(
        default_factory=list,
        description="Task ids that this clause is expected to map to",
    )


PlannerResponseKey: TypeAlias = Literal[
    "conversational.greeting",
    "conversational.appreciation",
    "conversational.checkin",
    "conversational.identity",
    "conversational.brand_origin",
    "conversational.capability_question",
    "conversational.casual_chat",
    "conversational.out_of_scope",
    "conversational.clarify",
    "planner.cancelled",
]

TransactionExecutor: TypeAlias = Literal["transfer", "airtime", "data"]
BeneficiaryRouteHint: TypeAlias = Literal["beneficiary_list", "recipient_ranking", "none"]
RouterDomainIntent: TypeAlias = Literal[
    "query",
    "account",
    "support",
    "beneficiary",
    "transfer",
    "airtime",
    "data",
]
AccountActionHint: TypeAlias = Literal[
    "list",
    "list_accounts",
    "count",
    "check_balance",
    "balance",
    "show_balance",
    "overall_balance",
    "link",
    "unlink",
    "set_default",
    "unknown",
    "none",
]

SemanticRoutingDecision: TypeAlias = Literal[
    "direct_reply",
    "direct_context_answer",
    "domain_query",
    "domain_account",
    "domain_support",
    "domain_beneficiary",
    "domain_transfer",
    "domain_airtime",
    "domain_data",
    "planner_mixed",
    "planner_ambiguous",
    "cancel",
]

SemanticRoutingMode: TypeAlias = Literal["new", "continuation", "quoted_replay", "active_flow_interrupt"]

ContextReadSubtype: TypeAlias = Literal[
    "account_count",
    "linked_accounts_summary",
    "default_account_identity",
    "pending_mandate_explanation",
    "account_mandate_readiness_summary",
    "account_linked_bank_existence_check",
    "beneficiary_count",
    "beneficiary_list",
    "beneficiary_existence_check",
    "beneficiary_name_match_preview",
    "flow_recap",
    "flow_missing_requirements",
]


InterruptRoutingDecision: TypeAlias = Literal[
    "continue_flow",
    "switch_intent",
    "cancel",
    "unclear",
    "approve_flow",
    "reject_flow",
    "status_query",
]

ContextFrameFollowupAction: TypeAlias = Literal[
    "answer_completeness",
    "lookup_entity",
    "show_details",
    "filter_items",
    "compare_items",
    "select_item",
    "explain_result",
    "replay_tasks",
    "start_new_task",
    "completeness_check",
    "entity_lookup",
    "detail_request",
    "selection",
    "replay",
    "new_task",
    "unclear",
]

ContextFrameRequestedField: TypeAlias = Literal[
    "amount",
    "bank",
    "counterparty",
    "date",
    "network",
    "phone",
    "reference",
    "status",
]

ContextFrameRank: TypeAlias = Literal["largest", "smallest", "newest", "oldest"]


class ContextFrameFollowupFilters(BaseModel):
    """Structured filters for grounding follow-ups against displayed result frames."""

    model_config = ConfigDict(extra="forbid")

    transaction_type: str | None = Field(
        default=None,
        description="Visible transaction type or task type filter, for example transfer, airtime, data, credit, debit",
    )
    status: str | None = Field(default=None, description="Visible status filter")
    direction: str | None = Field(default=None, description="Visible transaction direction filter")
    bank: str | None = Field(default=None, description="Visible bank name/reference filter")
    counterparty: str | None = Field(default=None, description="Visible counterparty/recipient/merchant filter")

PendingActionEditOperation: TypeAlias = Literal[
    "remove_tasks",
    "restore_tasks",
    "update_fields",
    "add_tasks",
    "approve_flow",
    "cancel_all",
    "status_query",
    "switch_intent",
    "unclear",
]


class PendingActionFieldUpdates(BaseModel):
    """Allowed pending confirmation field updates from semantic classification.

    This model is intentionally closed for OpenAI structured-output compatibility.
    The LLM may classify requested edits into these slots; deterministic code still
    validates whether each slot is applicable to the targeted task(s).
    """

    model_config = ConfigDict(extra="forbid")

    amount: float | None = Field(default=None, description="Updated transaction amount")
    narration: str | None = Field(default=None, description="Updated transfer narration")
    recipient_name: str | None = Field(default=None, description="Updated recipient/beneficiary reference")
    recipient_account: str | None = Field(default=None, description="Updated recipient account number")
    recipient_bank_name: str | None = Field(default=None, description="Updated recipient bank name")
    source_bank_name: str | None = Field(default=None, description="Updated source account bank reference")
    source_account_index: int | None = Field(default=None, description="1-based source account selection index")
    use_dual_accounts: bool | None = Field(default=None, description="Whether to pool funding across accounts")
    source_accounts: list[str] | None = Field(default=None, description="Source accounts/banks requested for pooling")
    funding_splits: list[FundingSplitUpdate] | None = Field(
        default=None,
        description="Explicit source-account funding split for a pending transfer",
    )
    phone: str | None = Field(default=None, description="Updated airtime/data phone number")
    network: str | None = Field(default=None, description="Updated airtime/data network")


class PendingActionTargetedUpdate(BaseModel):
    """One scoped edit against pending confirmation task(s)."""

    model_config = ConfigDict(extra="forbid")

    target_task_ids: list[str] = Field(
        default_factory=list,
        description="Task ids explicitly inferred from context for this scoped edit",
    )
    target_types: list[Literal["transfer", "airtime", "data"]] = Field(
        default_factory=list,
        description="Transaction task types targeted by this scoped edit",
    )
    target_texts: list[str] = Field(
        default_factory=list,
        description="Natural-language target references for this scoped edit",
    )
    fields: PendingActionFieldUpdates = Field(
        default_factory=PendingActionFieldUpdates,
        description="Field updates to apply to the resolved target task(s)",
    )


class PendingActionEditDecision(BaseModel):
    """LLM interpretation of a user turn relative to pending confirmation tasks.

    The model only classifies the semantic edit request. Deterministic code must
    still resolve task ids, validate ambiguity, and apply any state mutation.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_fields_object(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = data.pop("fields", None)
        if fields is None:
            return data
        if hasattr(fields, "model_dump"):
            fields = fields.model_dump(exclude_none=True)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if value is not None and key not in data:
                    data[key] = value
        return data

    operation: PendingActionEditOperation = Field(
        default="unclear",
        description="Semantic operation requested against the pending confirmation batch",
    )
    confidence: float = Field(default=0.0, description="Confidence in the pending-action edit interpretation")
    detected_language: str | None = Field(default=None, description="Detected language for the user turn")
    target_task_ids: list[str] = Field(
        default_factory=list,
        description="Task ids explicitly inferred from the pending task context; suggestions only",
    )
    target_types: list[Literal["transfer", "airtime", "data"]] = Field(
        default_factory=list,
        description="Transaction task types targeted by the edit",
    )
    target_texts: list[str] = Field(
        default_factory=list,
        description="Natural-language target references such as recipient, amount, bank, phone, or 'both transfers'",
    )
    updates: list[PendingActionTargetedUpdate] = Field(
        default_factory=list,
        description="Scoped field updates when one message edits multiple targets differently",
    )
    amount: float | None = Field(default=None, description="Updated transaction amount")
    narration: str | None = Field(default=None, description="Updated transfer narration")
    recipient_name: str | None = Field(default=None, description="Updated recipient/beneficiary reference")
    recipient_account: str | None = Field(default=None, description="Updated recipient account number")
    recipient_bank_name: str | None = Field(default=None, description="Updated recipient bank name")
    source_bank_name: str | None = Field(default=None, description="Updated source account bank reference")
    source_account_index: int | None = Field(default=None, description="1-based source account selection index")
    use_dual_accounts: bool | None = Field(default=None, description="Whether to pool funding across accounts")
    source_accounts: list[str] | None = Field(default=None, description="Source accounts/banks requested for pooling")
    funding_splits: list[FundingSplitUpdate] | None = Field(
        default=None,
        description="Explicit source-account funding split for a pending transfer",
    )
    phone: str | None = Field(default=None, description="Updated airtime/data phone number")
    network: str | None = Field(default=None, description="Updated airtime/data network")
    add_instruction: str | None = Field(
        default=None,
        description="Fresh user instruction to route when operation=add_tasks",
    )
    status_query_type: Literal["recap", "requirements"] | None = Field(
        default=None,
        description="Subtype when operation=status_query",
    )
    target_intent: str | None = Field(
        default=None,
        description="Intent to switch to when operation=switch_intent",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")

    @property
    def fields(self) -> PendingActionFieldUpdates:
        return PendingActionFieldUpdates(
            amount=self.amount,
            narration=self.narration,
            recipient_name=self.recipient_name,
            recipient_account=self.recipient_account,
            recipient_bank_name=self.recipient_bank_name,
            source_bank_name=self.source_bank_name,
            source_account_index=self.source_account_index,
            use_dual_accounts=self.use_dual_accounts,
            source_accounts=self.source_accounts,
            funding_splits=self.funding_splits,
            phone=self.phone,
            network=self.network,
        )


class ContextFrameFollowupDecision(BaseModel):
    """LLM interpretation of a user turn relative to the latest displayed response frame."""

    model_config = ConfigDict(extra="forbid")

    decision: ContextFrameFollowupAction = Field(
        default="unclear",
        description="Semantic action relative to the latest displayed frame",
    )
    confidence: float = Field(default=0.0, description="Confidence in the frame-follow-up interpretation")
    detected_language: str | None = Field(default=None, description="Detected language for the user turn")
    target_text: str | None = Field(
        default=None,
        description="User's referenced displayed entity, label, bank, recipient, group, or other visible target",
    )
    requested_field: ContextFrameRequestedField | None = Field(
        default=None,
        description="Specific safe displayed field the user asks about",
    )
    rank: ContextFrameRank | None = Field(
        default=None,
        description="Ranking selector when the user asks for largest/smallest/newest/oldest displayed item",
    )
    filters: ContextFrameFollowupFilters | None = Field(
        default=None,
        description="Structured filters to narrow displayed items",
    )
    selection_index: int | None = Field(
        default=None,
        description="1-based selected item index when the user chooses an item by number or ordinal",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")


class InterruptRouteDecision(BaseModel):
    """LLM decision for pending-input routing while a session is active."""

    decision: InterruptRoutingDecision = Field(
        description="Routing decision for pending-input turn",
    )
    confidence: float = Field(default=0.0, description="Confidence in routing decision (0.0-1.0)")
    detected_language: str | None = Field(
        default=None,
        description="Detected language for the turn",
    )
    target_intent: str | None = Field(
        default=None,
        description="Intent to switch to when decision=switch_intent",
    )
    target_mode: Literal["new", "continuation"] | None = Field(
        default=None,
        description="Optional routing mode hint (for example query new-vs-continuation)",
    )
    status_query_type: Literal["recap", "requirements"] | None = Field(
        default=None,
        description="Subtype when decision=status_query",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")


class SemanticRouteDecision(BaseModel):
    """LLM decision for first-pass semantic routing before planner-owned dispatch."""

    decision: SemanticRoutingDecision = Field(default="planner_ambiguous", description="Top-level routing action")
    confidence: float = Field(default=0.0, description="Confidence in routing decision (0.0-1.0)")
    detected_language: str | None = Field(default=None, description="Detected language for this turn")
    requested_language: str | None = Field(
        default=None,
        description="Explicit language requested for switch when user asks to change locale.",
    )
    mode: SemanticRoutingMode | None = Field(
        default=None,
        description="Optional routing mode hint such as new-vs-continuation semantics.",
    )
    target_intent: RouterDomainIntent | None = Field(
        default=None,
        description="Optional normalized domain owner for observability/debugging.",
    )
    response_key: PlannerResponseKey | None = Field(
        default=None,
        description="Deterministic keyed response when decision=direct_reply or cancel",
    )
    response: str | None = Field(
        default=None,
        description="Direct response text when decision=direct_reply or direct_context_answer",
    )
    expected_transaction_executors: list[TransactionExecutor] = Field(
        default_factory=list,
        description="Explicit transaction executors expected from planner, when known",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")


class PlannerOutput(BaseModel):
    """Structured output returned by the planner LLM.

    Combines classification and planning into single model.
    """

    # Classification fields
    primary_intent: str = Field(
        description=(
            "Primary intent: transfer, airtime, data, query, account, support, faq, conversational, cancel, mixed"
        )
    )
    response: str = Field(default="", description="Transitional acknowledgment text for non-keyed cases")
    response_key: PlannerResponseKey | None = Field(
        default=None,
        description="Deterministic keyed response for conversational/cancel paths",
    )
    confidence: float = Field(default=0.9, description="Confidence in classification (0.0-1.0)")
    is_complex: bool = Field(
        default=False, description="True if multiple recipients, mixed intents, or complex request"
    )
    is_cancellation: bool = Field(default=False, description="True if user wants to cancel/abort")
    is_confirmation: bool = Field(default=False, description="True if user explicitly confirms/agrees")
    detected_language: str | None = Field(
        default=None, description="Detected language: English, Yoruba, Hausa, Igbo, Pidgin, French"
    )
    context_read_subtype: ContextReadSubtype | None = Field(
        default=None,
        description=(
            "Set only for context-backed read-only account/beneficiary asks that are eligible for planner-owned "
            "context-read synthesis; otherwise null"
        ),
    )
    beneficiary_route: BeneficiaryRouteHint = Field(
        default="none",
        description=(
            "Beneficiary routing hint from planner: "
            "beneficiary_list when asking to view/manage saved beneficiaries, "
            "recipient_ranking when asking who user sends to most, otherwise none"
        ),
    )
    account_action_hint: AccountActionHint = Field(
        default="none",
        description=(
            "Account action hint for planner context-read disambiguation: "
            "list/list_accounts/count/check_balance/link/unlink/set_default, else none"
        ),
    )

    # Planning fields
    normalized_instruction: str = Field(default="", description="Cleaned up version of user request")
    clauses: list[PlannerClause] = Field(
        default_factory=list,
        description="Ordered semantic clause decomposition for the turn",
    )
    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str | None = None
    created_at: float = Field(default_factory=lambda: __import__("time").time())
