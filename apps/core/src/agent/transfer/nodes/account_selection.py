"""Account selection node for transfer flow."""

from apps.core.src.agent.common.nodes.account_selection import select_source_account_shared
from apps.core.src.agent.transfer.state import TransferState

from .utils import debug_log


def _validate_transfer_account(state: TransferState, selected: dict) -> TransferState | None:
    """Transfer-specific validation: ensure source account != recipient account."""
    selected_account_number = selected.get("account_number") or ""
    recipient_account = state.get("recipient_account")

    if not recipient_account or not selected_account_number:
        return None  # No validation needed

    # SAFEGUARD: Only flag error if BOTH account number AND bank match
    # Same account number in different banks is valid (e.g., 8162511023 in Opay vs Access Bank)
    recipient_bank_code = state.get("recipient_bank_code")
    recipient_bank_name = state.get("recipient_bank_name")
    source_bank_name = selected.get("bank_name") or ""
    source_bank_code = selected.get("bank_code") or ""

    if recipient_account == selected_account_number:
        # Account numbers match - check if banks also match
        bank_matches = False
        if recipient_bank_code and source_bank_code:
            bank_matches = recipient_bank_code == source_bank_code
        elif recipient_bank_name and source_bank_name:
            bank_matches = (recipient_bank_name.lower().strip()
                            == source_bank_name.lower().strip())

        if bank_matches:
            # BOTH account and bank match - this is an error
            debug_log(
                f"⚠️ select_source_account: DETECTED SOURCE ACCOUNT AS RECIPIENT! Account and bank match. source={selected_account_number}@{source_bank_name}, recipient={recipient_account}@{recipient_bank_name}")
            error_message = (
                f"The recipient account ({recipient_account}) at {recipient_bank_name or recipient_bank_code or 'the same bank'} "
                f"cannot be the same as your source account. Please provide a different recipient account."
            )
            return {
                **state,
                "selected_source_account": selected,
                "recipient_account": None,
                "recipient_bank_code": None,
                "recipient_bank_name": None,
                "recipient_name": None,
                "account_resolved": None,
                "matched_beneficiary": None,
                "response": error_message,
                "llm_reply": None,  # Clear llm_reply to prevent showing partial confirmation
            }
        else:
            # Account matches but bank differs - this is VALID
            debug_log(
                f"ℹ️ select_source_account: Account number matches source but bank differs. This is valid. source={selected_account_number}@{source_bank_name}, recipient={recipient_account}@{recipient_bank_name}")

    return None  # Validation passed


async def select_source_account(state: TransferState) -> TransferState:
    """Select source account for transfer flow."""
    accounts = state.get("accounts", [])
    source_account_id = state.get("source_account_id")
    debug_log(
        f"DEBUG select_source_account: accounts={len(accounts)}, source_account_id={source_account_id}")

    result = await select_source_account_shared(state, validator=_validate_transfer_account)

    selected = result.get("selected_source_account")
    debug_log(
        f"DEBUG select_source_account: selected={selected is not None}, response={bool(result.get('response'))}")

    return result
