"""State schema for the Utility Agent."""
from typing import Literal, List, Optional, TypedDict, NotRequired


class UtilityState(TypedDict):
    """State for the utility agent graph."""
    # User context
    phone_number: str
    message: str
    message_id: str
    messages: NotRequired[List]  # LangChain message history

    # Utility purchase details
    utility_type: NotRequired[Literal["airtime", "data", "other"]]
    amount: NotRequired[Optional[float]]
    recipient_phone: NotRequired[Optional[str]]  # For airtime/data for others
    network_provider: NotRequired[Optional[str]]  # MTN, Airtel, Glo, 9Mobile
    # Daily, Weekly, Monthly, Custom
    data_bundle_type: NotRequired[Optional[str]]

    # Source account
    source_account_id: NotRequired[Optional[str]]

    # Conversation management
    conversation_stage: NotRequired[
        Literal[
            "parsing",
            "validating",
            "confirming",
            "executing",
            "completed",
        ]
    ]

    missing_slots: NotRequired[List[str]]

    # Final response
    response: NotRequired[Optional[str]]

