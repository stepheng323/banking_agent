"""Routing logic for airtime purchase flow graph."""

from typing import Literal

from apps.core.src.agent.airtime.state import AirtimeState

from .utils import debug_log


def route_by_state(state: AirtimeState) -> Literal[
    "end",
    "collect_amount",
    "collect_phone",
    "select_account",
    "validate",
    "confirm",
    "cancel",
    "extract"
]:
    """Route based on current flow state and missing data."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    amount = state.get("amount")
    selected_account = state.get("selected_source_account")
    recipient_phone = state.get("recipient_phone")
    network = state.get("network")

    debug_log(
        f"DEBUG route_by_state: flow_state={flow_state}, has_response={bool(response)}")
    debug_log(
        f"DEBUG route_by_state: fields amount={amount}, selected_account={'yes' if selected_account else 'no'}, recipient_phone={recipient_phone}, network={network}")

    if flow_state == "cancelled":
        return "cancel" if not response else "end"

    llm_reply = state.get("llm_reply", "")
    has_response = response or llm_reply

    all_fields_present = amount and selected_account and recipient_phone and network
    if all_fields_present:
        amount_set_at = state.get("_amount_set_at")
        recipient_established_at = state.get("_recipient_established_at")
        seq_ok = True
        if amount_set_at is not None and recipient_established_at is not None:
            seq_ok = bool(amount_set_at >= recipient_established_at)
            debug_log(
                f"DEBUG route_by_state: sequencing check amt_ts={amount_set_at}, rcp_ts={recipient_established_at}, seq_ok={seq_ok}")
        if seq_ok:
            debug_log(
                "✅ route_by_state: All required fields present (sequence ok) -> confirm")
            return "confirm"

    if has_response and flow_state in ("collecting_amount", "collecting_phone", "selecting_account", "error", "confirming"):
        return "end"

    if flow_state == "validating":
        validation_errors = state.get("validation_errors", [])
        return "end" if validation_errors else "confirm"

    if flow_state == "confirming":
        return "end"

    if flow_state == "extracting":
        if all_fields_present and has_response:
            debug_log(
                "DEBUG route_by_state: All required fields present with response during extracting -> end")
            return "end"
        debug_log("DEBUG route_by_state: extracting -> validate")
        return "validate"

    if not amount:
        debug_log("DEBUG route_by_state: Missing amount -> collect_amount")
        return "collect_amount"

    if not selected_account:
        debug_log(
            "DEBUG route_by_state: No selected account, routing to select_account")
        return "select_account"

    if not recipient_phone or not network:
        debug_log(
            "DEBUG route_by_state: Missing recipient_phone or network -> collect_phone")
        return "collect_phone"

    return "end"


def route_after_extract(state: AirtimeState) -> str:
    """Route after extract node - check for cancellation."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    if flow_state == "cancelled" and not response:
        debug_log("🛑 Routing to cancel node after extract_entities")
        return "cancel"
    return "__route__"
