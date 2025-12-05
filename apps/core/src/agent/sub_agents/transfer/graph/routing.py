"""Routing logic for transfer flow graph."""

from typing import Literal

from apps.core.src.agent.sub_agents.transfer.state import TransferState

from .utils import debug_log


def route_by_state(state: TransferState) -> Literal["end", "collect_amount", "select_account", "collect_recipient", "validate", "check_changes", "confirm", "authorize", "cancel"]:
    """Route based on current flow state and missing data."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    transfer_status = state.get("transfer_status")
    debug_log(
        f"DEBUG route_by_state: flow_state={flow_state}, has_response={bool(response)}, transfer_status={transfer_status}")
    amount = state.get("amount")
    selected_account = state.get("selected_source_account")
    recipient_account = state.get("recipient_account")
    recipient_bank = state.get(
        "recipient_bank_code") or state.get("recipient_bank_name")
    account_resolved = state.get("account_resolved")
    debug_log(
        f"DEBUG route_by_state: fields amount={amount}, selected_account={'yes' if selected_account else 'no'}, recipient_account={recipient_account}, recipient_bank={recipient_bank}, account_resolved={account_resolved}")

    # Terminal states - always end
    if flow_state == "completed":
        debug_log("DEBUG route_by_state: flow_state=completed -> end")
        return "end"
    
    if flow_state == "error":
        debug_log("DEBUG route_by_state: flow_state=error -> end")
        return "end"
    
    # CRITICAL: If transfer_status is collection_complete, end the flow
    # This prevents proceeding to authorization for complex transfers
    if transfer_status == "collection_complete":
        debug_log("DEBUG route_by_state: transfer_status=collection_complete -> end (complex transfer, waiting for batch authorization)")
        return "end"

    if flow_state == "cancelled" and not response:
        return "cancel"

    if flow_state == "cancelled" and response:
        return "end"

    # Authorization flow - special handling
    if flow_state == "authorizing":
        pin_verified = state.get("pin_verified")
        debug_log(f"DEBUG route_by_state: flow_state=authorizing, pin_verified={pin_verified}")
        if pin_verified is True:
            debug_log("DEBUG route_by_state: PIN verified -> authorize")
            return "authorize"
        debug_log("DEBUG route_by_state: Waiting for PIN -> end")
        return "end"

    # Confirming state - route to confirm to send WhatsApp flow
    if flow_state == "confirming":
        debug_log("DEBUG route_by_state: flow_state=confirming -> confirm")
        return "confirm"

    if flow_state == "validating" and not account_resolved:
        if recipient_account and recipient_bank:
            return "validate"
        return "collect_recipient"

    llm_reply = state.get("llm_reply", "")
    has_response = response or llm_reply

    # Short-circuit: if all required fields are present, proceed to confirm
    # Only if the amount was set after recipient was established (when timestamps exist)
    if amount and selected_account and recipient_account and recipient_bank and account_resolved:
        amt_ts = state.get("_amount_set_at")
        rcp_ts = state.get("_recipient_established_at")
        seq_ok = True
        if amt_ts is not None and rcp_ts is not None:
            seq_ok = bool(amt_ts >= rcp_ts)
            debug_log(
                f"DEBUG route_by_state: sequencing check amt_ts={amt_ts}, rcp_ts={rcp_ts}, seq_ok={seq_ok}")
        if seq_ok:
            debug_log(
                "✅ route_by_state: All required fields present (sequence ok) -> confirm")
            return "confirm"

    if has_response and flow_state in ("collecting_amount", "selecting_account", "collecting_recipient", "error", "confirming"):
        return "end"

    if not amount:
        debug_log("DEBUG route_by_state: Missing amount -> collect_amount")
        return "collect_amount"

    if not selected_account:
        debug_log(
            "DEBUG route_by_state: No selected account, routing to select_account")
        return "select_account"

    if not recipient_account or not recipient_bank:
        debug_log(
            f"🔍 [ROUTING] Missing recipient -> collect_recipient (recipient_account={recipient_account}, recipient_bank={recipient_bank})")
        return "collect_recipient"

    if flow_state == "validating":

        change_acknowledged = state.get("_change_acknowledged", False)
        if not change_acknowledged and account_resolved:
            return "check_changes"
        return "confirm"

    if flow_state == "confirming":
        return "end"

    if flow_state == "extracting":
        if (amount and selected_account and recipient_account and recipient_bank and
                account_resolved and has_response):
            debug_log(
                "DEBUG route_by_state: All required fields present with response during extracting -> end")
            return "end"
        debug_log("DEBUG route_by_state: extracting -> validate")
        return "validate"

    return "end"


def route_after_extract(state: TransferState) -> str:
    """Route after extract node - check for cancellation."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    if flow_state == "cancelled" and not response:
        debug_log("🛑 Routing to cancel node after extract_entities")
        return "cancel"
    return "load_context"
