"""Change detection node for transfer flow."""

from typing import cast
import json

from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.cache.bank_cache import BankCacheService

from .utils import debug_log


async def check_and_acknowledge_changes(
    state: TransferState,
    bank_cache: BankCacheService,
) -> TransferState:
    """
    Check for changes in transfer details and show acknowledgment message.
    Returns state with acknowledgment message if changes detected.
    """
    # Only check if we have a transfer in progress (account already resolved)
    account_resolved = state.get("account_resolved")
    selected_account = state.get("selected_source_account")

    if not account_resolved or not selected_account:
        # Missing required data, mark as acknowledged to prevent routing loop and proceed
        return {
            **state,
            "_change_acknowledged": True,
            "flow_state": "confirming",
        }

    # If we already acknowledged changes, proceed to confirmation to avoid routing loop
    if state.get("_change_acknowledged"):
        return {
            **state,
            "flow_state": "confirming",
        }

    # Get current values
    current_amount = state.get("amount")
    current_account = state.get("recipient_account")
    current_bank_code = state.get("recipient_bank_code")
    current_bank_name = state.get("recipient_bank_name")
    current_recipient_name = state.get("recipient_name")

    # Get previous values from Redis
    phone_number = state.get("phone_number")
    idem_key = state.get("idempotency_key")

    if not phone_number or not idem_key:
        # Missing phone_number or idem_key, cannot store previous values
        # Mark as acknowledged and proceed to confirmation since no previous values to compare
        return {
            **state,
            "_change_acknowledged": True,
            "flow_state": "confirming",
        }

    prev_key = f"transfer:prev:{phone_number}:{idem_key}"
    try:
        prev_data = await bank_cache.redis.get(prev_key)
        if not prev_data:
            # No previous data, store current as previous and mark as acknowledged
            prev_values = {
                "amount": current_amount,
                "recipient_account": current_account,
                "recipient_bank_code": current_bank_code,
                "recipient_bank_name": current_bank_name,
                "recipient_name": current_recipient_name or (
                    account_resolved.get("account_name") if isinstance(
                        account_resolved, dict) else None
                ),
            }
            await bank_cache.redis.set(
                prev_key,
                json.dumps(prev_values),
                ex=3600
            )
            # Mark as acknowledged and proceed to confirmation since no previous data to compare
            return {
                **state,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            }

        prev_values = json.loads(prev_data)
        previous_amount = prev_values.get("amount")
        previous_account = prev_values.get("recipient_account")
        previous_bank_code = prev_values.get("recipient_bank_code")
        previous_bank_name = prev_values.get("recipient_bank_name")
        previous_recipient_name = prev_values.get("recipient_name")
    except Exception as e:
        debug_log(f"⚠️  Error loading previous values from Redis: {e}")
        return state

    # Detect changes
    changes = []
    recipient_info_changed = False

    if current_amount and previous_amount and current_amount != previous_amount:
        amount_str = f"₦{current_amount:,.0f}"
        if current_amount == int(current_amount):
            amount_str = amount_str.replace('.0', '')
        changes.append(f"amount to {amount_str}")

    if current_account and previous_account and str(current_account) != str(previous_account):
        changes.append(f"account number to {current_account}")
        recipient_info_changed = True

    if current_bank_code and previous_bank_code and str(current_bank_code) != str(previous_bank_code):
        bank_name = current_bank_name or current_bank_code
        changes.append(f"bank to {bank_name}")
        recipient_info_changed = True
    elif current_bank_name and previous_bank_name and current_bank_name != previous_bank_name:
        changes.append(f"bank to {current_bank_name}")
        recipient_info_changed = True

    if current_recipient_name and previous_recipient_name and current_recipient_name != previous_recipient_name:
        changes.append(f"recipient to {current_recipient_name.title()}")
        recipient_info_changed = True

    # If recipient info changed, clear account_resolved to trigger re-validation
    new_state_updates = {}
    if recipient_info_changed:
        debug_log(
            f"DEBUG check_and_acknowledge_changes: Recipient info changed, clearing account_resolved for re-validation")
        new_state_updates["account_resolved"] = None

        # Clear matched_beneficiary only if it doesn't match the new recipient
        # (find_beneficiary may have already matched a new beneficiary, so we should preserve it)
        matched_beneficiary = state.get("matched_beneficiary")
        if matched_beneficiary and isinstance(matched_beneficiary, dict):
            beneficiary_account = str(
                matched_beneficiary.get("account_number", ""))
            beneficiary_bank_code = str(
                matched_beneficiary.get("bank_code", ""))
            # Only clear if it doesn't match the new recipient
            if (beneficiary_account != str(current_account) or
                    beneficiary_bank_code != str(current_bank_code)):
                debug_log(f"DEBUG check_and_acknowledge_changes: Clearing stale matched_beneficiary "
                          f"(beneficiary: {beneficiary_account}/{beneficiary_bank_code} != "
                          f"current: {current_account}/{current_bank_code})")
                new_state_updates["matched_beneficiary"] = None
            else:
                debug_log(f"DEBUG check_and_acknowledge_changes: Preserving matched_beneficiary "
                          f"(still matches new recipient: {current_account}/{current_bank_code})")

    # If changes detected, show acknowledgment
    if changes:
        if len(changes) == 1:
            message = f"Ok, changing {changes[0]}."
        elif len(changes) == 2:
            message = f"Ok, changing {changes[0]} and {changes[1]}."
        else:
            message = f"Ok, changing {', '.join(changes[:-1])}, and {changes[-1]}."

        # If recipient info changed, we need to re-validate first
        # The routing logic will prioritize re-validation (when account_resolved is None) over sending responses
        if recipient_info_changed:
            # Clear account_resolved to trigger re-validation
            # Set response - routing will prioritize re-validation when account_resolved is None
            result = {
                **state,
                **new_state_updates,
                "response": message,  # Acknowledgment message - will be sent after re-validation
                "_change_acknowledged": True,  # Mark as acknowledged to prevent immediate re-check
                "flow_state": "validating",  # Route to validate to re-resolve account
            }
        else:
            # Only amount changed, no re-validation needed, show acknowledgment and proceed to confirmation
            result = {
                **state,
                "response": message,
                "_change_acknowledged": True,
                "flow_state": "confirming",
            }
        return cast(TransferState, result)

    # No changes detected, update previous values in Redis and proceed to confirmation
    prev_values = {
        "amount": current_amount,
        "recipient_account": current_account,
        "recipient_bank_code": current_bank_code,
        "recipient_bank_name": current_bank_name,
        "recipient_name": current_recipient_name or (
            account_resolved.get("account_name") if isinstance(
                account_resolved, dict) else None
        ),
    }
    await bank_cache.redis.set(
        prev_key,
        json.dumps(prev_values),
        ex=3600
    )

    # Mark as acknowledged and proceed to confirmation since no changes detected
    return {
        **state,
        "_change_acknowledged": True,
        "flow_state": "confirming",
    }

