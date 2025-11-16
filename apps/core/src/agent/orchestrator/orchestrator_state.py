"""State for the orchestrator."""
from typing import Optional, TypedDict


class OrchestratorState(TypedDict):
    """Minimal state kept while agent layers are stripped."""

    phone_number: str
    message: str
    message_id: str
    response: str
    intent: Optional[str]
    is_complex: Optional[bool]
