"""Response intent definitions for unified response generation."""

from enum import Enum


class ResponseIntent(Enum):
    """Structured response intents for all subgraphs.
    
    Subgraphs return these intents instead of hardcoded strings,
    and the ResponseSynthesizer converts them to natural language.
    """
    
    # Collection intents
    ASK_AMOUNT = "ask_amount"
    ASK_RECIPIENT = "ask_recipient"
    ASK_BANK = "ask_bank"
    ASK_ACCOUNT_NUMBER = "ask_account_number"
    ASK_PHONE_NUMBER = "ask_phone_number"
    ASK_NETWORK = "ask_network"
    ASK_DATA_PLAN = "ask_data_plan"
    
    # Clarification intents
    CLARIFY_BENEFICIARY = "clarify_beneficiary"
    CLARIFY_ACCOUNT = "clarify_account"
    CLARIFY_BANK = "clarify_bank"
    
    # Confirmation intents
    CONFIRM_TRANSFER = "confirm_transfer"
    CONFIRM_AIRTIME = "confirm_airtime"
    CONFIRM_DATA = "confirm_data"
    
    # Success intents
    TRANSFER_SUCCESS = "transfer_success"
    AIRTIME_SUCCESS = "airtime_success"
    DATA_SUCCESS = "data_success"
    
    # Error intents
    TRANSFER_FAILED = "transfer_failed"
    AIRTIME_FAILED = "airtime_failed"
    DATA_FAILED = "data_failed"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    ACCOUNT_NOT_FOUND = "account_not_found"
    ACCOUNT_VALIDATION_FAILED = "account_validation_failed"
    BANK_NOT_FOUND = "bank_not_found"
    SESSION_EXPIRED = "session_expired"
    PIN_FAILED = "pin_failed"
    MAX_ATTEMPTS_EXCEEDED = "max_attempts_exceeded"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_PHONE_NUMBER = "invalid_phone_number"
    INVALID_NETWORK = "invalid_network"
    
    # Flow control intents
    CANCELLED = "cancelled"
    MANDATE_REQUIRED = "mandate_required"
    ACCOUNT_SELECTION_REQUIRED = "account_selection_required"
    
    # Conversational intents
    GREETING = "greeting"
    HELP = "help"
    BALANCE_RESPONSE = "balance_response"
    TRANSACTION_HISTORY = "transaction_history"
    BENEFICIARY_SAVED = "beneficiary_saved"
    
    # Acknowledgment intents
    RECIPIENT_CHANGED = "recipient_changed"
    AMOUNT_CHANGED = "amount_changed"
