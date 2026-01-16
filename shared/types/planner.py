"""Models for task planning and normalization."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class PlannedTask(BaseModel):
    """Structured representation of a single planned task."""

    task_id: str = Field(..., description="Stable ID referenced by depends_on")
    action: str
    executor: Literal["query", "transfer", "airtime", "data", "utility", "system", "tool", "manage_accounts"]
    instruction: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    idempotency_key: str | None = None  # Set by engine: "{user_id}:{workflow_id}:{task_id}"


class PlannerOutput(BaseModel):
    """Structured output returned by the planner LLM."""

    normalized_instruction: str
    primary_intent: Literal["query", "transfer", "utility", "mixed", "conversational"]
    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str | None = None
    created_at: float = Field(default_factory=lambda: __import__("time").time())
