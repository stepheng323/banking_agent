"""State for the agent."""

from typing import Annotated, Any, Dict, List, Literal, NotRequired, Optional, TypedDict
from langgraph.graph.message import add_messages
from shared.models.account import Account
from shared.types import IntentType, SecurityLevel
from shared.types.agent_types import Task


class AgentState(TypedDict):
    """State for the banking agent graph."""
    phone_number: str
    message: str
    message_id: str

    user_id: NotRequired[str]
    intent: NotRequired[IntentType]
    confidence: NotRequired[float]
    security_level: NotRequired[SecurityLevel]

    accounts: NotRequired[List[Account]]
    balance: NotRequired[float]
    selected_account: NotRequired[Account]
    transfer_amount: NotRequired[float]
    recipient_account: NotRequired[Account]
    recipient_name: NotRequired[str]
    recipient_bank_name: NotRequired[str]

    pin_verified: NotRequired[bool]
    requires_pin: NotRequired[bool]
    pin_attempts: NotRequired[int]

    faq_context: NotRequired[str]
    messages: Annotated[list, add_messages] 
    awating_confirmation: NotRequired[bool]
    clarification_needed: NotRequired[Optional[str]]

    response: NotRequired[str]
    response_type: NotRequired[Literal["text", "flow", "confirmation"]]

    error: NotRequired[str]
    retry_count: NotRequired[int]
    metadata: NotRequired[Dict[str, Any]]

    task_plan: NotRequired[List[Task]]
    current_task_index: NotRequired[int]
    tasks_completed: NotRequired[List[str]]
