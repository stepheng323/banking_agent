"""State schema for the Transfer Agent."""
from typing import Literal, List, Optional, TypedDict, NotRequired


class RecipientDetails(TypedDict):
    """Recipient information for transfer."""
    name: NotRequired[Optional[str]]
    matched_beneficiary_id: NotRequired[Optional[str]]
    account_number: NotRequired[Optional[str]]
    bank_code: NotRequired[Optional[str]]
    bank_name: NotRequired[Optional[str]]
    resolved_account_name: NotRequired[Optional[str]]
    confidence_score: NotRequired[Optional[float]]
    is_new_beneficiary: NotRequired[bool]


class AmountDetails(TypedDict):
    """Amount information for transfer."""
    value: NotRequired[Optional[float]]
    currency: NotRequired[str]
    needs_calculation: NotRequired[bool]
    calculation_expression: NotRequired[Optional[str]]
    source_data: NotRequired[Optional[dict]]


class SourceAccountDetails(TypedDict):
    """Source account information."""
    account_id: NotRequired[Optional[str]]
    account_name: NotRequired[Optional[str]]
    balance: NotRequired[Optional[float]]


class TransferDetails(TypedDict):
    """Complete transfer details."""
    recipient: NotRequired[RecipientDetails]
    amount: NotRequired[AmountDetails]
    source_account: NotRequired[SourceAccountDetails]
    purpose: NotRequired[Optional[str]]
    notes: NotRequired[Optional[str]]


class TransferState(TypedDict):
    """State for the transfer agent graph."""
    phone_number: str
    message: str
    message_id: str
    messages: NotRequired[List]

    transfer_details: NotRequired[TransferDetails]
    conversation_stage: NotRequired[
        Literal[
            "parsing",
            "enriching",
            "gathering",
            "planning",
            "validating",
            "confirming",
            "executing",
            "completed",
        ]
    ]

    missing_slots: NotRequired[List[str]]
    clarifications_needed: NotRequired[List[dict]]
    execution_plan: NotRequired[Optional[List[dict]]]
    validation_result: NotRequired[Optional[dict]]
    dependencies: NotRequired[List[str]]

    user_accounts: NotRequired[Optional[List[dict]]]
    user_beneficiaries: NotRequired[Optional[List[dict]]]

    pending_clarification: NotRequired[Optional[dict]]
    waiting_for_user_response: NotRequired[bool]
    waiting_for_confirmation: NotRequired[bool]

    awaiting_clarification: NotRequired[Optional[bool]]
    clarification_type: NotRequired[Optional[str]]

    response: NotRequired[Optional[str]]
