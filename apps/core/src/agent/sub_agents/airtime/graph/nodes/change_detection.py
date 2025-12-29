"""Change detection node for airtime purchase flow."""

import json
from typing import cast

from apps.core.src.agent.sub_agents.airtime.state import AirtimeState

from ..context import AirtimeNodeContext


async def check_and_acknowledge_changes(state: AirtimeState) -> AirtimeState:
    """
    Check for changes in airtime purchase details and show acknowledgment message.
    Returns state with acknowledgment message if changes detected.
    """
    ctx = AirtimeNodeContext.get()
    if ctx.redis_client is None:
        raise ValueError("redis_client not set in AirtimeNodeContext")

    selected_account = state.get("selected_source_account")

    if not selected_account:
        return cast(
            AirtimeState,
            {
                **state,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            },
        )

    if state.get("_change_acknowledged"):
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "confirming",
            },
        )

    current_amount = state.get("amount")
    current_recipient_phone = state.get("recipient_phone")
    current_network = state.get("network")
    current_recipient_name = state.get("recipient_name")

    phone_number = state.get("phone_number")

    if not phone_number:
        return cast(
            AirtimeState,
            {
                **state,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            },
        )

    # Use phone-number-only key for change tracking
    # This allows us to track changes even when idempotency_key is reset or changed
    # The key persists across the user's airtime purchase session
    prev_key = f"airtime:prev:session:{phone_number}"
    try:
        prev_data = await ctx.redis_client.get(prev_key)
        if not prev_data:
            prev_values = {
                "amount": current_amount,
                "recipient_phone": current_recipient_phone,
                "network": current_network,
                "recipient_name": current_recipient_name,
            }
            await ctx.redis_client.setex(prev_key, 3600, json.dumps(prev_values))
            # No previous values to compare, so no changes to acknowledge
            return cast(
                AirtimeState,
                {
                    **state,
                    "response": "",  # Clear any stale response
                    "_change_acknowledged": True,
                    "flow_state": "confirming",
                },
            )

        prev_values = json.loads(prev_data)
        previous_amount = prev_values.get("amount")
        previous_recipient_phone = prev_values.get("recipient_phone")
        previous_network = prev_values.get("network")
        previous_recipient_name = prev_values.get("recipient_name")

    except Exception:
        return state

    changes = []
    recipient_info_changed = False

    if current_amount and previous_amount and current_amount != previous_amount:
        amount_str = f"₦{current_amount:,.0f}"
        if current_amount == int(current_amount):
            amount_str = amount_str.replace(".0", "")
        changes.append(f"amount to {amount_str}")

    if (
        current_recipient_phone
        and previous_recipient_phone
        and str(current_recipient_phone) != str(previous_recipient_phone)
    ):
        changes.append(f"phone number to {current_recipient_phone}")
        recipient_info_changed = True

    if current_network and previous_network and str(current_network) != str(previous_network):
        changes.append(f"network to {current_network}")
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
            beneficiary_phone = str(matched_beneficiary.get("account_number", ""))
            beneficiary_network = str(matched_beneficiary.get("bank_code", ""))
            if beneficiary_phone != str(current_recipient_phone) or beneficiary_network != str(
                current_network
            ):
                new_state_updates["matched_beneficiary"] = None

    if changes:
        if len(changes) == 1:
            message = f"Ok, changing {changes[0]}."
        elif len(changes) == 2:
            message = f"Ok, changing {changes[0]} and {changes[1]}."
        else:
            message = f"Ok, changing {', '.join(changes[:-1])}, and {changes[-1]}."

        result = cast(
            AirtimeState,
            {
                **state,
                **new_state_updates,
                "response": message,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            },
        )

        # Update previous values in Redis for next comparison
        prev_values = {
            "amount": current_amount,
            "recipient_phone": current_recipient_phone,
            "network": current_network,
            "recipient_name": current_recipient_name,
        }
        await ctx.redis_client.setex(prev_key, 3600, json.dumps(prev_values))

        return result

    # Update previous values in Redis for next comparison
    prev_values = {
        "amount": current_amount,
        "recipient_phone": current_recipient_phone,
        "network": current_network,
        "recipient_name": current_recipient_name,
    }
    await ctx.redis_client.setex(prev_key, 3600, json.dumps(prev_values))

    # No changes detected - clear any previous response and mark acknowledgment complete
    return cast(
        AirtimeState,
        {
            **state,
            "response": "",
            "_change_acknowledged": True,
            "flow_state": "confirming",
        },
    )
