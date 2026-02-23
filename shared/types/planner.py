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
    international: bool | None = None
    alias: str | None = None
    reference: ContextReference | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None


class PlannedTask(BaseModel):
    """Structured representation of a single planned task."""

    task_id: str = Field(..., description="Stable ID referenced by depends_on")
    action: str
    executor: Literal["transfer", "query", "airtime", "data", "account", "support", "faq", "beneficiary"]
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


InterruptRoutingDecision: TypeAlias = Literal["continue_flow", "switch_intent", "cancel", "unclear"]


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

    # Planning fields
    normalized_instruction: str = Field(default="", description="Cleaned up version of user request")
    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str | None = None
    created_at: float = Field(default_factory=lambda: __import__("time").time())
