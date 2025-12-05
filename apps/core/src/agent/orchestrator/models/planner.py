"""Models for task planning and normalization."""

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from shared.types.agent_types import TaskStatus


class PlannedTask(BaseModel):
    """Structured representation of a single planned task."""

    id: str
    action: str
    executor: Literal["query", "transfer", "airtime", "data", "utility", "system", "tool"]
    instruction: str
    description: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    condition: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING


class PlannerOutput(BaseModel):
    """Structured output returned by the planner LLM."""

    normalized_instruction: str
    primary_intent: Literal["query", "transfer",
                            "utility", "mixed", "conversational"]
    tasks: List[PlannedTask] = Field(default_factory=list)
    notes: Optional[str] = None
