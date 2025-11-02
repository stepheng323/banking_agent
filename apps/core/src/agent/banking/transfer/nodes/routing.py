# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Routing functions for the transfer agent."""
from apps.core.src.agent.banking.transfer.transfer_state import TransferState


def route_after_slot_validation(state: TransferState) -> str:
    """Route based on slot validation result."""
    if state.get("missing_slots") or state.get("clarifications_needed"):
        return "gathering"

    # Check if we need to resolve account (new beneficiary with bank details but no resolved name)
    transfer_details = state.get("transfer_details", {})
    recipient = transfer_details.get("recipient", {})

    if recipient.get("is_new_beneficiary") and recipient.get("account_number") and recipient.get("bank_code"):
        # Need resolution if account_number and bank_code exist but resolved_account_name doesn't
        if not recipient.get("resolved_account_name"):
            return "resolving"

    return "planning"


def route_after_validation(state: TransferState) -> str:
    """Route after validation."""
    if state.get("validation_result", {}).get("valid"):
        return "confirming"
    return "completed"


def route_after_confirmation(state: TransferState) -> str:
    """Route after confirmation response."""
    if state.get("conversation_stage") == "executing":
        return "executing"
    if state.get("conversation_stage") == "gathering":
        return "gathering"
    return "completed"
