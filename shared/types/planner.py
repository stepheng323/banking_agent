"""Models for task planning and normalization."""

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field


class ContextReference(BaseModel):
    """Pointer to a context entity."""

    selector: Literal["previous", "index", "label"]
    index: int | None = None
    label: str | None = None


class TaskParameters(BaseModel):
    """Common parameters for tasks."""

    amount: str | float | None = None
    recipient: str | None = None
    recipient_name: str | None = None
    narration: str | None = None
    recipient_phone: str | None = None
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


PlannerResponseKey: TypeAlias = Literal[
    "conversational.greeting",
    "conversational.appreciation",
    "conversational.checkin",
    "conversational.identity",
    "conversational.brand_origin",
    "conversational.capability_question",
    "conversational.out_of_scope",
    "conversational.clarify",
    "planner.cancelled",
]

TransactionExecutor: TypeAlias = Literal["transfer", "airtime", "data"]
BeneficiaryRouteHint: TypeAlias = Literal["beneficiary_list", "recipient_ranking", "none"]
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

TurnRoutingDecision: TypeAlias = Literal["go_planner", "respond_directly", "query_continuation"]

ContextFastpathSubtype: TypeAlias = Literal[
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


class TurnRouteDecision(BaseModel):
    """LLM decision for low-cost pre-planner routing."""

    decision: TurnRoutingDecision = Field(default="go_planner", description="Routing action before planner")
    confidence: float = Field(default=0.0, description="Confidence in routing decision (0.0-1.0)")
    detected_language: str | None = Field(default=None, description="Detected language for this turn")
    response_key: PlannerResponseKey | None = Field(
        default=None,
        description="Deterministic keyed response when decision=respond_directly",
    )
    response: str | None = Field(default=None, description="Direct response text when keyed response is unavailable")
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
    context_fastpath_subtype: ContextFastpathSubtype | None = Field(
        default=None,
        description=(
            "Set only for context-backed read-only account/beneficiary asks that are eligible for fastpath; "
            "otherwise null"
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
            "Account action hint for context-fastpath disambiguation: "
            "list/list_accounts/count/check_balance/link/unlink/set_default, else none"
        ),
    )

    # Planning fields
    normalized_instruction: str = Field(default="", description="Cleaned up version of user request")
    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str | None = None
    created_at: float = Field(default_factory=lambda: __import__("time").time())
