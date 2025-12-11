"""Account selection node for transfer flow."""

from apps.core.src.agent.tools.account_selection.node import select_source_account_shared
from apps.core.src.agent.sub_agents.transfer.state import TransferState

from .utils import debug_log


def _validate_transfer_account(state: TransferState, selected: dict) -> TransferState | None:
    """
    Transfer-specific validation: ensure source account != recipient account.
    
    Delegates to SelfTransferValidator for consistent validation logic.
    """
    from apps.core.src.agent.sub_agents.transfer.validators import SelfTransferValidator
    
    recipient_account = state.get("recipient_account")
    if not recipient_account:
        return None
    
    validator = SelfTransferValidator()
    is_valid, error_message = validator.validate(
        recipient_account=recipient_account,
        recipient_bank_code=state.get("recipient_bank_code"),
        recipient_bank_name=state.get("recipient_bank_name"),
        source_account=selected,
    )
    
    if not is_valid:
        debug_log(
            f"⚠️ select_source_account: DETECTED SOURCE ACCOUNT AS RECIPIENT! {error_message}")
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
            "llm_reply": None,
        }
    
    return None


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
