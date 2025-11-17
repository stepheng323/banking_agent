"""LangGraph state for airtime purchase flow."""

from typing import Literal, NotRequired, Optional, TypedDict


class AirtimeState(TypedDict):
    """State for airtime purchase flow graph."""

    phone_number: str
    message: str
    message_id: str

    active_flow: Literal["transfer", "airtime", "data", None]
    flow_state: Literal[
        "extracting",
        "collecting_amount",
        "collecting_phone",
        "selecting_account",
        "validating",
        "confirming",
        "authorizing",
        "completed",
        "error",
        "cancelled"
    ]

    amount: Optional[float]
    recipient_phone: Optional[str]
    network: Optional[str]
    source_account_id: Optional[str]
    narration: Optional[str]

    missing_fields: list[str]

    user_profile: Optional[dict]
    accounts: list[dict]
    beneficiaries: list[dict]

    selected_source_account: Optional[dict]
    matched_beneficiary: Optional[dict]

    balance_available: Optional[float]
    validation_errors: list[str]

    response: str
    llm_reply: Optional[str]

    recipient_name: Optional[str]

    idempotency_key: Optional[str]
    airtime_status: Literal["pending", "confirmed",
                            "authorized", "completed", "failed", None]

    classification_result: NotRequired[Optional[dict]]
