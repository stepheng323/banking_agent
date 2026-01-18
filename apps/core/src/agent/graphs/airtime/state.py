"""LangGraph state for airtime purchase flow."""

from typing import Literal, NotRequired, TypedDict


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
        "cancelled",
        "negotiating",
    ]

    amount: float | None
    recipient_phone: str | None
    network: str | None
    source_account_id: str | None
    narration: str | None

    missing_fields: list[str]

    user_profile: dict | None
    accounts: list[dict]
    beneficiaries: list[dict]

    selected_source_account: dict | None
    matched_beneficiary: dict | None

    balance_available: float | None
    validation_errors: list[str]

    response: str
    llm_reply: str | None

    recipient_name: str | None

    idempotency_key: str | None
    airtime_status: Literal["pending", "confirmed", "authorized", "completed", "failed", None]

    classification_result: NotRequired[dict | None]

    pin_verified: NotRequired[bool | None]
    pin_verification_error: NotRequired[str | None]
    pin_retry_count: NotRequired[int]

    language: NotRequired[str | None]

    # Capability negotiation: stored when user needs to accept/reject alternative
    pending_negotiation: NotRequired[dict | None]  # {type, suggested_action, patch}

