"""State schema for the Transfer Agent."""
from typing import Literal, List, Optional, TypedDict, NotRequired


class RecipientDetails(TypedDict):
    """Recipient information for transfer."""
    name: NotRequired[Optional[str]]  # "mummy", "John Doe"
    matched_beneficiary_id: NotRequired[Optional[str]]  # Internal ID if found
    account_number: NotRequired[Optional[str]]
    bank_code: NotRequired[Optional[str]]
    bank_name: NotRequired[Optional[str]]
    confidence_score: NotRequired[Optional[float]]  # Match confidence 0-100
    is_new_beneficiary: NotRequired[bool]


class AmountDetails(TypedDict):
    """Amount information for transfer."""
    value: NotRequired[Optional[float]]
    currency: NotRequired[str]  # "NGN"
    needs_calculation: NotRequired[bool]  # True for "5% of balance"
    calculation_expression: NotRequired[Optional[str]]  # "balance * 0.05"
    source_data: NotRequired[Optional[dict]]  # Data needed for calculation


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
    purpose: NotRequired[Optional[str]]  # "allowance", "rent", "tithe"
    notes: NotRequired[Optional[str]]


class TransferState(TypedDict):
    """State for the transfer agent graph."""
    # User context
    phone_number: str
    message: str
    message_id: str
    messages: NotRequired[List]  # LangChain message history

    # Transfer details (slots to fill)
    transfer_details: NotRequired[TransferDetails]

    # Conversation management
    conversation_stage: NotRequired[
        Literal[
            "parsing",  # Extracting intent
            "enriching",  # Loading context
            "gathering",  # Asking for missing info
            "planning",  # Creating execution plan
            "validating",  # Checking business rules
            "confirming",  # Waiting for user approval
            "executing",  # Performing transfer
            "completed",  # Done
        ]
    ]

    missing_slots: NotRequired[List[str]]  # ["recipient.account_number", "amount.value"]
    clarifications_needed: NotRequired[List[dict]]  # [{"type": "ambiguous_recipient", "options": [...]}]
    execution_plan: NotRequired[Optional[List[dict]]]  # [{"step": "check_balance", "tool": "get_account_balance"}]
    validation_result: NotRequired[Optional[dict]]  # {"valid": bool, "errors": [...]}
    dependencies: NotRequired[List[str]]  # ["check_balance"]

    # User context (cached for session)
    user_accounts: NotRequired[Optional[List[dict]]]
    user_beneficiaries: NotRequired[Optional[List[dict]]]

    # Conversation control
    pending_clarification: NotRequired[Optional[dict]]
    waiting_for_user_response: NotRequired[bool]
    waiting_for_confirmation: NotRequired[bool]

    # Final response
    response: NotRequired[Optional[str]]

