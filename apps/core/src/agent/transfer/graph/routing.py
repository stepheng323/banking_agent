"""Routing logic for transfer flow graph."""

from typing import Literal

from apps.core.src.agent.transfer.state import TransferState

from .utils import debug_log


def route_by_state(state: TransferState) -> Literal["end", "collect_amount", "select_account", "collect_recipient", "validate", "check_changes", "confirm", "cancel"]:
    """Route based on current flow state and missing data."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    amount = state.get("amount")
    selected_account = state.get("selected_source_account")
    recipient_account = state.get("recipient_account")
    recipient_bank = state.get(
        "recipient_bank_code") or state.get("recipient_bank_name")
    account_resolved = state.get("account_resolved")

    # Check if cancellation was detected but not yet handled
    # If flow_state is "cancelled" but response is empty, route to cancel node
    if flow_state == "cancelled" and not response:
        return "cancel"

    # If already cancelled and response is set, end to send it
    if flow_state == "cancelled" and response:
        return "end"

    # Priority: If account_resolved was cleared (recipient info changed), re-validate first
    # This takes priority over sending responses to ensure account is re-validated
    if flow_state == "validating" and not account_resolved:
        # Make sure we have recipient info to validate
        if recipient_account and recipient_bank:
            return "validate"
        # If no recipient info, go back to collecting
        return "collect_recipient"

    # If we have a response, end to send it (unless we need to re-validate)
    # Also check llm_reply as fallback for cases where response wasn't set but llm_reply exists
    llm_reply = state.get("llm_reply", "")
    has_response = response or llm_reply
    if has_response and flow_state in ("collecting_amount", "selecting_account", "collecting_recipient", "error", "confirming"):
        return "end"

    if not amount:
        return "collect_amount"

    if not selected_account:
        debug_log(
            f"DEBUG route_by_state: No selected account, routing to select_account")
        return "select_account"

    if not recipient_account or not recipient_bank:
        return "collect_recipient"

    if flow_state == "validating":

        change_acknowledged = state.get("_change_acknowledged", False)
        if not change_acknowledged and account_resolved:
            return "check_changes"
        return "confirm"

    if flow_state == "confirming":
        return "end"

    if flow_state == "extracting":
        # If all fields are complete and we have a response (e.g., narration was added), end to send it
        # Otherwise, validate to ensure everything is correct
        if (amount and selected_account and recipient_account and recipient_bank and 
            account_resolved and has_response):
            return "end"
        return "validate"

    return "end"


def route_after_extract(state: TransferState) -> str:
    """Route after extract node - check for cancellation."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    if flow_state == "cancelled" and not response:
        debug_log(f"🛑 Routing to cancel node after extract_entities")
        return "cancel"
    return "load_context"

