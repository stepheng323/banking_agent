"""LangGraph state for transfer flow."""

from typing import Literal, NotRequired, Optional, TypedDict


class TransferState(TypedDict):
    """State for transfer flow graph."""

    phone_number: str

    message: str
    message_id: str

    active_flow: Literal["transfer", "airtime", "data", None]
    flow_state: Literal[
        "extracting",
        "collecting_amount",
        "collecting_recipient",
        "selecting_account",
        "validating",
        "confirming",
        "authorizing",
        "completed",
        "error",
        "cancelled"
    ]

    # Extracted entities
    amount: Optional[float]
    recipient_name: Optional[str]
    recipient_account: Optional[str]
    recipient_bank_code: Optional[str]
    recipient_bank_name: Optional[str]
    source_account_id: Optional[str]
    narration: Optional[str]

    missing_fields: list[str]

    user_profile: Optional[dict]
    accounts: list[dict]
    beneficiaries: list[dict]

    selected_source_account: Optional[dict]
    matched_beneficiary: Optional[dict]

    account_resolved: Optional[dict]
    balance_available: Optional[float]
    validation_errors: list[str]

    response: str
    llm_reply: Optional[str]

    idempotency_key: Optional[str]
    transfer_status: Literal["pending", "confirmed",
                             "authorized", "completed", "failed", None]

    # Classification result from orchestrator
    classification_result: NotRequired[Optional[dict]]

    # PIN verification fields
    pin_verified: NotRequired[Optional[bool]]
    pin_verification_error: NotRequired[Optional[str]]
    pin_retry_count: NotRequired[int]
    
    # User Preferences
    language: NotRequired[Optional[str]]

    # Change tracking fields
    _previous_amount: NotRequired[Optional[float]]
    _previous_recipient_account: NotRequired[Optional[str]]
    _previous_recipient_bank_code: NotRequired[Optional[str]]
    _previous_recipient_bank_name: NotRequired[Optional[str]]
    _previous_recipient_name: NotRequired[Optional[str]]
    _change_acknowledged: NotRequired[bool]
