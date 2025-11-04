"""State schema for the Transfer Agent."""
from typing import Annotated, Literal, List, Optional, TypedDict, NotRequired, Any
from operator import add


def merge_transfer_details(existing: Optional[dict], new: Optional[dict]) -> dict:
    """Custom reducer to merge transfer_details dicts, preserving nested data."""
    if not existing:
        return new or {}
    if not new:
        return existing
    # Deep merge: preserve existing nested dicts and update with new values
    merged = {**existing}
    for key, value in new.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


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
    allocated_amount: NotRequired[Optional[float]]


class AmountDetails(TypedDict):
    """Amount information for transfer."""
    value: NotRequired[Optional[float]]
    currency: NotRequired[str]
    needs_calculation: NotRequired[bool]
    calculation_expression: NotRequired[Optional[str]]
    source_data: NotRequired[Optional[dict]]
    total_value: NotRequired[Optional[float]]
    per_recipient_value: NotRequired[Optional[float]]
    split_strategy: NotRequired[Optional[str]]
    participants: NotRequired[Optional[int]]


class SourceAccountDetails(TypedDict):
    """Source account information."""
    account_id: NotRequired[Optional[str]]
    account_name: NotRequired[Optional[str]]
    balance: NotRequired[Optional[float]]


class TransferDetails(TypedDict):
    """Complete transfer details."""
    recipient: NotRequired[RecipientDetails]
    recipients: NotRequired[List[RecipientDetails]]
    current_recipient_index: NotRequired[int]
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

    # Use custom reducer to deep merge transfer_details across updates
    transfer_details: NotRequired[Annotated[dict, merge_transfer_details]]
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
    execution_plan: NotRequired[Annotated[Optional[List[dict]], add]]
    validation_result: NotRequired[Annotated[Optional[dict],
                                             merge_transfer_details]]
    dependencies: NotRequired[List[str]]

    # Parallel execution support: use reducers to merge results from concurrent nodes
    user_accounts: NotRequired[Annotated[List[dict], add]]
    user_beneficiaries: NotRequired[Annotated[List[dict], add]]
    all_beneficiaries: NotRequired[Annotated[List[dict], add]]

    # Use custom reducer for dict fields to preserve across updates
    pending_clarification: NotRequired[Annotated[Optional[dict],
                                                 merge_transfer_details]]
    waiting_for_user_response: NotRequired[bool]
    waiting_for_confirmation: NotRequired[bool]

    awaiting_clarification: NotRequired[Optional[bool]]
    clarification_type: NotRequired[Optional[str]]

    response: NotRequired[Optional[str]]

    # Outbox for deferred side-effects (e.g., WhatsApp flows). Append-only via reducer
    outbox_messages: NotRequired[Annotated[List[dict], add]]
