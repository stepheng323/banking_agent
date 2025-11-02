"""State definition for the orchestrator graph."""

from typing import Any, Dict, List, Literal, Optional, TypedDict

from typing_extensions import NotRequired

from apps.core.src.agent.models.user_context import UserContext
from apps.core.src.agent.models.corrected_intent import CorrectedIntent, DisambiguatedIntent
from apps.core.src.agent.models.planner import PlannedTask


class OrchestratorState(TypedDict):
    """State for the orchestrator graph flow."""

    # Input
    phone_number: str
    message: str
    message_id: str

    # Normalization pipeline
    user_context: NotRequired[Optional[UserContext]]
    corrected_intent: NotRequired[Optional[CorrectedIntent]]
    disambiguated_intent: NotRequired[Optional[DisambiguatedIntent]]

    # Planning
    normalized_instruction: NotRequired[Optional[str]]
    primary_intent: NotRequired[Optional[Literal["query",
                                                 "transfer", "utility", "mixed", "conversational"]]]
    task_plan: NotRequired[List[PlannedTask]]
    planner_notes: NotRequired[Optional[str]]

    # Execution
    task_results: NotRequired[List[Dict[str, Any]]]

    # Output
    response: NotRequired[str]

    # Conversation tracking
    awaiting_clarification: NotRequired[bool]
    clarification_type: NotRequired[Optional[str]]
    is_continuation: NotRequired[bool]
    # Early intent classification result
    quick_classification: NotRequired[Optional[str]]
