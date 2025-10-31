"""State for the Orchestrator supergraph."""
from typing import Literal, NotRequired, Optional, TypedDict


class OrchestratorState(TypedDict):
    """Unified state for the orchestrator that routes to specialized agents."""
    phone_number: str
    message: str
    message_id: str
    
    classified_intent: NotRequired[Literal["query", "transfer", "utility"]]
    classification_confidence: NotRequired[float]
    classification_reasoning: NotRequired[str]
    
    response: NotRequired[Optional[str]]
    error: NotRequired[Optional[str]]

