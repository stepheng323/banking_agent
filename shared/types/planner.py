"""Models for task planning and normalization."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskParameters(BaseModel):
    """Common parameters for tasks."""
    amount: str | float | None = None
    recipient: str | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    recipient_account: str | None = None
    bank_name: str | None = None
    phone: str | None = None
    budget: str | None = None
    plan: str | None = None
    is_self: bool = False
    # Note: user_text was removed - it caused LLM to auto-populate it, breaking new transfers



class PlannedTask(BaseModel):
    """Structured representation of a single planned task."""

    task_id: str = Field(..., description="Stable ID referenced by depends_on")
    action: str
    executor: Literal[
        "transfer", "query", "airtime", "data",
        "account_management", "support", "faq"
    ]
    instruction: str
    description: str | None = None
    parameters: TaskParameters = Field(default_factory=TaskParameters)
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    risk: Literal["READ_ONLY", "MUTATION", "MONEY_MOVE"] = "READ_ONLY"
    idempotency_key: str | None = None  # Set by engine


class PlannerOutput(BaseModel):
    """Structured output returned by the planner LLM.
    
    Combines classification and planning into single model.
    """

    # Classification fields
    primary_intent: str = Field(
        description="Primary intent: transfer, airtime, data, query, account_management, support, faq, conversational, cancel, mixed"
    )
    response: str = Field(
        default="",
        description="Short acknowledgment message for the user"
    )
    confidence: float = Field(
        default=0.9,
        description="Confidence in classification (0.0-1.0)"
    )
    is_complex: bool = Field(
        default=False,
        description="True if multiple recipients, mixed intents, or complex request"
    )
    is_cancellation: bool = Field(
        default=False,
        description="True if user wants to cancel/abort"
    )
    detected_language: str | None = Field(
        default=None,
        description="Detected language: English, Yoruba, Hausa, Igbo, Pidgin, French"
    )
    
    # Planning fields
    normalized_instruction: str = Field(
        default="",
        description="Cleaned up version of user request"
    )
    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str | None = None
    created_at: float = Field(default_factory=lambda: __import__("time").time())

