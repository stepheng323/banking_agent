"""State for the orchestrator."""

from typing import TypedDict


class OrchestratorState(TypedDict):
    """Minimal state kept while agent layers are stripped."""

    phone_number: str
    message: str
    message_id: str
    response: str
    intent: str | None
    is_complex: bool | None
