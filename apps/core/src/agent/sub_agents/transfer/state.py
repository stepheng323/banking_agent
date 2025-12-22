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
        "cancelled",
        "checking_funding",
        "planning_funding",
        "confirming_funding",
        "initiating_debits",
        "awaiting_debits",
        "initiating_payout",
    ]

    amount: Optional[float]
    recipient_name: Optional[str]
    recipient_account: Optional[str]
    recipient_bank_code: Optional[str]
    recipient_bank_name: Optional[str]
    source_account_id: Optional[str]
    source_bank_name: Optional[str]
    is_internal_transfer: Optional[bool]
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

    classification_result: NotRequired[Optional[dict]]

    pin_verified: NotRequired[Optional[bool]]
    pin_verification_error: NotRequired[Optional[str]]
    pin_retry_count: NotRequired[int]
    
    language: NotRequired[Optional[str]]

    _previous_amount: NotRequired[Optional[float]]
    _previous_recipient_account: NotRequired[Optional[str]]
    _previous_recipient_bank_code: NotRequired[Optional[str]]
    _previous_recipient_bank_name: NotRequired[Optional[str]]
    _previous_recipient_name: NotRequired[Optional[str]]
    _change_acknowledged: NotRequired[bool]
    
    image_data: NotRequired[Optional[str]]

    # Multi-account funding fields
    funding_required: NotRequired[bool]
    funding_plan: NotRequired[Optional[dict]]
    funding_steps: NotRequired[list[dict]]
    funded_transfer_id: NotRequired[Optional[str]]
    funding_status: NotRequired[Literal[
        "pending",
        "user_confirming",
        "debiting",
        "funded",
        "payout_pending",
        "completed",
        "failed",
        "refunding",
        None
    ]]
    funding_error: NotRequired[Optional[str]]

