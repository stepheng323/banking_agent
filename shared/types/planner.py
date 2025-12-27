"""Models for task planning and normalization."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from shared.types.agent_types import TaskStatus


class PlannedTask(BaseModel):
    """Structured representation of a single planned task."""

    id: str
    action: str
    executor: Literal[
        "query", "transfer", "airtime", "data", "utility", "system", "tool", "manage_accounts"
    ]
    instruction: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    status: TaskStatus = TaskStatus.PENDING


class PlannerOutput(BaseModel):
    """Structured output returned by the planner LLM."""

    normalized_instruction: str
    primary_intent: Literal["query", "transfer", "utility", "mixed", "conversational"]
    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str | None = None
