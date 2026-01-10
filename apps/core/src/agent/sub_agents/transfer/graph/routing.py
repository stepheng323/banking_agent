"""Routing logic for transfer flow graph."""

from typing import Literal

from apps.core.src.agent.sub_agents.transfer.graph.nodes.utils import debug_log
from apps.core.src.agent.sub_agents.transfer.state import TransferState


def route_by_state(
    state: TransferState,
) -> Literal[
    "end",
    "collect_amount",
    "select_account",
    "collect_recipient",
    "validate",
    "check_changes",
    "confirm",
    "authorize",
    "cancel",
    "initiate_debits",
]:
    """Route based on current flow state and missing data."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    transfer_status = state.get("transfer_status")
    debug_log(
        f"DEBUG route_by_state: flow_state={flow_state}, has_response={bool(response)}, transfer_status={transfer_status}"
    )
    amount = state.get("amount")
    selected_account = state.get("selected_source_account")
    recipient_account = state.get("recipient_account")
    recipient_bank = state.get("recipient_bank_code") or state.get("recipient_bank_name")
    account_resolved = state.get("account_resolved")
    debug_log(
        f"DEBUG route_by_state: fields amount={amount}, selected_account={'yes' if selected_account else 'no'}, recipient_account={recipient_account}, recipient_bank={recipient_bank}, account_resolved={account_resolved}"
    )

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
        debug_log(
            "DEBUG route_by_state: transfer_status=collection_complete -> end (complex transfer, waiting for batch authorization)"
        )
        return "end"

    # CRITICAL: If transfer_status is pending, confirmation was already sent
    # Don't route to confirm again - wait for PIN verification
    if transfer_status == "pending":
        debug_log("DEBUG route_by_state: transfer_status=pending -> end (confirmation already sent, waiting for PIN)")
        return "end"

    if flow_state == "cancelled" and not response:
        return "cancel"

    if flow_state == "cancelled" and response:
        return "end"

    # User approved funding plan - go to initiate debits
    funding_approved = state.get("funding_approved")
    if funding_approved and flow_state == "confirming_funding":
        debug_log("DEBUG route_by_state: funding_approved=True -> initiate_debits")
        return "initiate_debits"

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
            debug_log(f"DEBUG route_by_state: sequencing check amt_ts={amt_ts}, rcp_ts={rcp_ts}, seq_ok={seq_ok}")
        if seq_ok:
            from shared.utils.logging import get_logger

            logger = get_logger(__name__)
            logger.info(
                "route_by_state_ROUTING_TO_CONFIRM",
                amount=amount,
                selected_account=bool(selected_account),
                recipient_account=recipient_account,
                recipient_bank=recipient_bank,
                account_resolved=bool(account_resolved),
            )
            debug_log("✓ route_by_state: All required fields present (sequence ok) -> confirm")
            return "confirm"

    if has_response and flow_state in (
        "collecting_amount",
        "selecting_account",
        "collecting_recipient",
        "error",
        "confirming",
        "authorizing",
    ):
        return "end"

    transfer_all = state.get("transfer_all")
    transfer_percentage = state.get("transfer_percentage")
    if not amount and not transfer_all and not transfer_percentage:
        debug_log("DEBUG route_by_state: Missing amount -> collect_amount")
        return "collect_amount"

    if not selected_account:
        if flow_state == "selecting_account":
            return "end"
        if not state.get("accounts"):
            return "end"
        debug_log("DEBUG route_by_state: No selected account, routing to select_account")
        return "select_account"

    if not recipient_account or not recipient_bank:
        debug_log(
            f"🔍 [ROUTING] Missing recipient -> collect_recipient (recipient_account={recipient_account}, recipient_bank={recipient_bank})"
        )
        return "collect_recipient"

    if flow_state == "validating":
        change_acknowledged = state.get("_change_acknowledged", False)
        if not change_acknowledged and account_resolved:
            return "check_changes"
        return "confirm"

    if flow_state == "confirming":
        return "end"

    if flow_state == "extracting":
        transfer_percentage = state.get("transfer_percentage")
        has_amount = amount or transfer_all or transfer_percentage
        if (
            has_amount
            and selected_account
            and recipient_account
            and recipient_bank
            and account_resolved
            and has_response
        ):
            debug_log("DEBUG route_by_state: All required fields present with response during extracting -> end")
            return "end"
        # If we need to validate but don't have all fields, route appropriately
        if not selected_account:
            return "select_account"
        if not recipient_account or not recipient_bank:
            return "collect_recipient"
        debug_log("DEBUG route_by_state: extracting -> validate")
        return "validate"

    return "end"


def route_after_extract(state: TransferState) -> str:
    """Route after extract node - check for cancellation or authorization."""
    flow_state = state.get("flow_state")
    transfer_status = state.get("transfer_status")
    response = state.get("response", "")

    debug_log(
        f"🔀 route_after_extract: flow_state={flow_state}, transfer_status={transfer_status}, has_response={bool(response)}"
    )

    # If authorizing, skip to authorize node
    if flow_state == "authorizing":
        debug_log("✓ Routing to authorize node after extract (PIN verified)")
        return "authorize"

    # User approved funding plan - go straight to initiate debits
    # User approved funding plan - verify approval type
    funding_approved = state.get("funding_approved")
    # Also check if PIN was just verified (via flow callback)
    pin_verified = state.get("pin_verified")

    if funding_approved or pin_verified:
        debug_log("✓ Funding approved/verified -> verify_funding")
        return "verify_funding"

    if flow_state == "cancelled" and not response:
        debug_log("🛑 Routing to cancel node after extract_entities")
        return "cancel"

    debug_log("➡️ Routing to load_context after extract")
    return "load_context"


def route_after_verification(state: TransferState) -> str:
    """Route after verifying funding approval."""
    funding_approved = state.get("funding_approved")

    if funding_approved:
        debug_log("✓ Funding verified -> initiate_debits")
        return "initiate_debits"

    debug_log("⏳ Funding confirmation pending -> end")
    return "end"


def route_after_funding_check(state: TransferState) -> str:
    """Route after funding-related nodes based on funding state."""
    flow_state = state.get("flow_state")
    funding_required = state.get("funding_required", False)
    funding_status = state.get("funding_status")

    debug_log(
        f"🔀 route_after_funding_check: flow_state={flow_state}, funding_required={funding_required}, funding_status={funding_status}"
    )

    if flow_state == "error":
        debug_log("❌ Funding error -> end")
        return "error"

    if flow_state == "authorizing":
        # For normal transfers (funding_required=False):
        # - Before PIN: stop execution (return end)
        # - After PIN: continue to authorize
        if not funding_required:
            pin_verified = state.get("pin_verified", False)
            if pin_verified:
                debug_log("✓ Normal transfer PIN verified -> authorize")
                return "authorize"
            debug_log("✓ Normal transfer PIN flow sent, stopping execution -> end")
            return "end"
        # For funded transfers, continue to authorize after debits complete
        debug_log("✓ Funded transfer ready -> authorize")
        return "authorize"

    if flow_state == "planning_funding":
        debug_log("📊 Need funding plan -> plan_funding")
        return "plan_funding"

    if flow_state == "confirming_funding":
        debug_log("❓ Awaiting user funding confirmation -> confirm_funding")
        return "confirm_funding"

    if flow_state == "awaiting_amount_adjustment":
        debug_log("💰 Insufficient funds, awaiting user amount adjustment -> end")
        return "error"  # Routes to END, user can adjust amount

    if flow_state == "initiating_debits" or funding_status == "debiting":
        debug_log("💳 Debits in progress -> authorize (will poll)")
        return "authorize"

    if flow_state == "awaiting_debits":
        debug_log("⏳ Debits still pending -> wait_for_debits (polling)")
        return "wait_for_debits"

    if flow_state == "initiating_payout" or funding_status == "funded":
        debug_log("✓ Debits complete -> authorize")
        from shared.utils.logging import get_logger

        logger = get_logger(__name__)
        logger.info(
            "route_after_funding_check_ROUTING_TO_AUTHORIZE",
            flow_state=flow_state,
            funding_status=funding_status,
        )
        return "authorize"

    if not funding_required:
        debug_log("✓ No funding required -> authorize")
        return "authorize"

    debug_log("📊 Funding required -> plan_funding")
    return "plan_funding"
