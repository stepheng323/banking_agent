"""LangGraph state for transfer flow."""

from typing import Literal, NotRequired, TypedDict


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


    amount: float | None
    recipient_name: str | None
    recipient_account: str | None
    recipient_bank_code: str | None
    recipient_bank_name: str | None
    source_account_id: str | None
    source_bank_name: str | None
    is_internal_transfer: bool | None
    narration: str | None

    missing_fields: list[str]

    user_profile: dict | None
    accounts: list[dict]
    beneficiaries: list[dict]

    selected_source_account: dict | None
    matched_beneficiary: dict | None

    account_resolved: dict | None
    balance_available: float | None
    validation_errors: list[str]

    response: str
    llm_reply: str | None

    idempotency_key: str | None
    transfer_status: Literal["pending", "confirmed", "authorized", "completed", "failed", None]

    classification_result: NotRequired[dict | None]

    pin_verified: NotRequired[bool | None]
    pin_verification_error: NotRequired[str | None]
    pin_retry_count: NotRequired[int]

    language: NotRequired[str | None]

    _previous_amount: NotRequired[float | None]
    _previous_recipient_account: NotRequired[str | None]
    _previous_recipient_bank_code: NotRequired[str | None]
    _previous_recipient_bank_name: NotRequired[str | None]
    _previous_recipient_name: NotRequired[str | None]
    _change_acknowledged: NotRequired[bool]

    image_data: NotRequired[str | None]

    # Multi-account funding fields
    funding_required: NotRequired[bool]
    funding_plan: NotRequired[dict | None]
    funding_steps: NotRequired[list[dict]]
    funded_transfer_id: NotRequired[str | None]
    funding_status: NotRequired[
        Literal[
            "pending",
            "user_confirming",
            "debiting",
            "funded",
            "payout_pending",
            "completed",
            "failed",
            "refunding",
            None,
        ]
    ]
    funding_error: NotRequired[str | None]
    funding_rejected: NotRequired[bool]
    funding_approved: NotRequired[bool]

    source_accounts: NotRequired[list[str] | None]
    use_dual_accounts: NotRequired[bool | None]
    explicit_split: NotRequired[dict[str, float] | None]
    transfer_all: NotRequired[bool | None]  # True when user wants to send entire balance
    transfer_percentage: NotRequired[float | None]  # Percentage of balance to transfer (e.g., 10 for 10%)

    awaiting_confirmation: NotRequired[bool]
    confirmation_context: NotRequired[dict | None]
    confirmation_token: NotRequired[str | None]
    confirmation_summary: NotRequired[str | None]
