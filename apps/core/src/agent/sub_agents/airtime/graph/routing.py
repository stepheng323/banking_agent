"""Routing logic for airtime purchase flow graph."""

from typing import Literal

from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def route_by_state(state: AirtimeState) -> Literal[
    "end",
    "collect_amount",
    "collect_phone",
    "select_account",
    "validate",
    "confirm",
    "authorize",
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

    logger.debug("route_by_state", 
                flow_state=flow_state,
                has_response=bool(response),
                has_amount=bool(amount),
                has_account=bool(selected_account),
                has_phone=bool(recipient_phone),
                has_network=bool(network))

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
        if seq_ok:
            return "confirm"

    if has_response and flow_state in ("collecting_amount", "collecting_phone", "selecting_account", "error", "confirming"):
        return "end"

    if flow_state == "validating":
        validation_errors = state.get("validation_errors", [])
        return "end" if validation_errors else "confirm"

    if flow_state == "confirming":
        return "end"

    if flow_state == "authorizing":
        return "authorize"

    if flow_state == "extracting":
        if all_fields_present and has_response:
            return "end"
        return "validate"

    if not amount:
        return "collect_amount"

    if not selected_account:
        return "select_account"

    if not recipient_phone or not network:
        return "collect_phone"

    return "end"


def route_after_extract(state: AirtimeState) -> str:
    """Route after extract node - check for cancellation."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    if flow_state == "cancelled" and not response:
        return "cancel"
    return "__route__"
