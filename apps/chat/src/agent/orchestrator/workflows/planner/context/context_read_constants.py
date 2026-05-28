"""Shared constants for planner context-read handling."""

CONTEXT_READ_LIST_LIMIT = 5
CONTEXT_READ_ACCOUNT_SUBTYPES = {
    "account_count",
    "linked_accounts_summary",
    "default_account_identity",
    "pending_mandate_explanation",
    "account_mandate_readiness_summary",
    "account_linked_bank_existence_check",
}
CONTEXT_READ_BENEFICIARY_SUBTYPES = {
    "beneficiary_count",
    "beneficiary_list",
    "beneficiary_existence_check",
    "beneficiary_name_match_preview",
}
CONTEXT_READ_FLOW_SUBTYPES = {
    "flow_recap",
    "flow_missing_requirements",
}
CONTEXT_READ_SUBTYPES = (
    CONTEXT_READ_ACCOUNT_SUBTYPES | CONTEXT_READ_BENEFICIARY_SUBTYPES | CONTEXT_READ_FLOW_SUBTYPES
)
TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
BENEFICIARY_MATCH_PREVIEW_LIMIT = 3
BENEFICIARY_CONTEXT_READ_PERSIST_SUBTYPES = {"beneficiary_list", "beneficiary_name_match_preview"}
NO_ACTIVE_FLOW_CONTEXT_READ_MESSAGE = (
    "There is no active transfer flow right now. Start a transfer and I will guide you."
)


__all__ = [
    "BENEFICIARY_CONTEXT_READ_PERSIST_SUBTYPES",
    "BENEFICIARY_MATCH_PREVIEW_LIMIT",
    "CONTEXT_READ_ACCOUNT_SUBTYPES",
    "CONTEXT_READ_BENEFICIARY_SUBTYPES",
    "CONTEXT_READ_FLOW_SUBTYPES",
    "CONTEXT_READ_LIST_LIMIT",
    "CONTEXT_READ_SUBTYPES",
    "NO_ACTIVE_FLOW_CONTEXT_READ_MESSAGE",
    "TRANSACTION_EXECUTORS",
]
