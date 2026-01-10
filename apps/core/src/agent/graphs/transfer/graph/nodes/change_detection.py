"""Change detection node for transfer flow."""

from typing import cast

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.bank_cache import BankCacheService


async def check_and_acknowledge_changes(
    state: TransferState,
    bank_cache: BankCacheService,
) -> TransferState:
    """
    Check for changes in transfer details and show acknowledgment message.
    Returns state with acknowledgment message if changes detected.
    """
    selected_account = state.get("selected_source_account")

    if not selected_account:
        return cast(
            TransferState,
            {
                **state,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            },
        )

    if state.get("_change_acknowledged"):
        return cast(
            TransferState,
            {
                **state,
                "flow_state": "confirming",
            },
        )

    current_amount = state.get("amount")
    current_recipient_account = state.get("recipient_account")
    current_recipient_bank_code = state.get("recipient_bank_code")
    current_recipient_bank_name = state.get("recipient_bank_name")
    current_recipient_name = state.get("recipient_name")

    # Get previous values from state
    previous_amount = state.get("_previous_amount")
    previous_recipient_account = state.get("_previous_recipient_account")
    state.get("_previous_recipient_bank_code")
    previous_recipient_bank_name = state.get("_previous_recipient_bank_name")
    previous_recipient_name = state.get("_previous_recipient_name")

    # If no previous values exist, store current and proceed
    if previous_amount is None and previous_recipient_account is None:
        return cast(
            TransferState,
            {
                **state,
                "_previous_amount": current_amount,
                "_previous_recipient_account": current_recipient_account,
                "_previous_recipient_bank_code": current_recipient_bank_code,
                "_previous_recipient_bank_name": current_recipient_bank_name,
                "_previous_recipient_name": current_recipient_name,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            },
        )

    changes = []
    recipient_info_changed = False

    if current_amount and previous_amount and current_amount != previous_amount:
        amount_str = f"₦{current_amount:,.0f}"
        if current_amount == int(current_amount):
            amount_str = amount_str.replace(".0", "")
        changes.append(f"amount to {amount_str}")

    if (
        current_recipient_account
        and previous_recipient_account
        and str(current_recipient_account) != str(previous_recipient_account)
    ):
        changes.append(f"account number to {current_recipient_account}")
        recipient_info_changed = True

    if (
        current_recipient_bank_name
        and previous_recipient_bank_name
        and str(current_recipient_bank_name) != str(previous_recipient_bank_name)
    ):
        changes.append(f"bank to {current_recipient_bank_name}")
        recipient_info_changed = True

    if (
        current_recipient_name
        and previous_recipient_name
        and current_recipient_name != previous_recipient_name
    ):
        changes.append(f"recipient to {current_recipient_name.title()}")
        recipient_info_changed = True

    new_state_updates = {}
    if recipient_info_changed:
        matched_beneficiary = state.get("matched_beneficiary")
        if matched_beneficiary and isinstance(matched_beneficiary, dict):
            beneficiary_account = str(matched_beneficiary.get("account_number", ""))
            beneficiary_bank_code = str(matched_beneficiary.get("bank_code", ""))
            if beneficiary_account != str(
                current_recipient_account
            ) or beneficiary_bank_code != str(current_recipient_bank_code):
                new_state_updates["matched_beneficiary"] = None

    if changes:
        if len(changes) == 1:
            message = f"Ok, changing {changes[0]}."
        elif len(changes) == 2:
            message = f"Ok, changing {changes[0]} and {changes[1]}."
        else:
            message = f"Ok, changing {', '.join(changes[:-1])}, and {changes[-1]}."

        return cast(
            TransferState,
            {
                **state,
                **new_state_updates,
                "response": message,
                "_previous_amount": current_amount,
                "_previous_recipient_account": current_recipient_account,
                "_previous_recipient_bank_code": current_recipient_bank_code,
                "_previous_recipient_bank_name": current_recipient_bank_name,
                "_previous_recipient_name": current_recipient_name,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            },
        )

    # No changes detected - clear any previous response and mark acknowledgment complete
    return cast(
        TransferState,
        {
            **state,
            "_previous_amount": current_amount,
            "_previous_recipient_account": current_recipient_account,
            "_previous_recipient_bank_code": current_recipient_bank_code,
            "_previous_recipient_bank_name": current_recipient_bank_name,
            "_previous_recipient_name": current_recipient_name,
            "response": "",
            "_change_acknowledged": True,
            "flow_state": "confirming",
        },
    )
