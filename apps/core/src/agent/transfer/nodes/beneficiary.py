"""Beneficiary matching node for transfer flow."""

from typing import cast

from shared.database.models import Beneficiary
from shared.utils.serialization import sqlalchemy_to_dict

from apps.core.src.agent.services.beneficiary_matcher import BeneficiaryMatcher
from apps.core.src.agent.transfer.state import TransferState

from .utils import debug_log


async def find_beneficiary(
    state: TransferState,
    matcher: BeneficiaryMatcher,
) -> TransferState:
    """Find beneficiary by name (optional convenience feature). Account resolution happens via banking API."""
    rec_name = state.get("recipient_name")
    acct_number = state.get("recipient_account")
    bank_code = state.get("recipient_bank_code")
    bank_name = state.get("recipient_bank_name")
    beneficiaries = state.get("beneficiaries", [])

    if rec_name and not (acct_number and (bank_code or bank_name)):
        beneficiaries_models = [
            Beneficiary(**b) if isinstance(b, dict) else b
            for b in beneficiaries
        ]
        status, single, candidates = matcher.match(
            rec_name, beneficiaries_models)

        if status == "single" and single:
            return {
                **state,
                "recipient_account": str(single.account_number),
                "recipient_bank_code": str(single.bank_code),
                "recipient_bank_name": str(single.bank_name),
                "recipient_name": str(single.account_name or single.alias or rec_name),
                "matched_beneficiary": sqlalchemy_to_dict(single) if hasattr(single, "__table__") else single,
            }
        elif status == "clarify" and candidates:
            opts = "; ".join([
                f"{(b.account_name or b.alias)} ({b.bank_name} • {str(b.account_number)[-4:]})"
                for b in candidates
            ])
            return {
                **state,
                "matched_beneficiary": None,  # Clear stale beneficiary - need clarification
                "flow_state": "collecting_recipient",
                # Use explicit clarification prompt to avoid conflicting LLM replies
                "response": f"I found multiple matches for '{rec_name}'. Which one? {opts}",
            }
        else:
            # No match found - clear any stale matched_beneficiary
            # If user has already provided account number but not bank, ask only for bank
            if acct_number and not (bank_code or bank_name):
                return {
                    **state,
                    "matched_beneficiary": None,
                    "flow_state": "collecting_recipient",
                    "response": "Which bank is that for?",
                }
            else:
                return {
                    **state,
                    "matched_beneficiary": None,  # Clear stale beneficiary - no match found
                    "flow_state": "collecting_recipient",
                    # Prefer explicit prompt here
                    "response": "Please provide the account number and bank name.",
                }

    if acct_number and not (bank_code or bank_name):
        # User provided account but not bank - clear any stale matched_beneficiary
        # (since we're collecting bank info, the beneficiary match is no longer valid)
        return {
            **state,
            "matched_beneficiary": None,  # Clear stale beneficiary - bank info missing
            "flow_state": "collecting_recipient",
            # Prefer explicit prompt for missing bank
            "response": "Which bank is that for?",
        }

    # If bank is present but account is missing, ask only for account number
    if (bank_code or bank_name) and not acct_number:
        return {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_recipient",
            "response": "What is the account number?",
        }

    if not acct_number or not (bank_code or bank_name):
        # Missing account or bank - clear any stale matched_beneficiary
        return {
            **state,
            "matched_beneficiary": None,  # Clear stale beneficiary - missing info
            "flow_state": "collecting_recipient",
            # Prefer explicit combined prompt
            "response": "Please provide the account number and bank name.",
        }

    # If we have account and bank but no matched_beneficiary, that's fine
    # (user provided account details directly, not through beneficiary matching)
    # Clear any stale matched_beneficiary if it doesn't match current recipient
    matched_beneficiary = state.get("matched_beneficiary")
    updates = {}

    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        beneficiary_account = str(
            matched_beneficiary.get("account_number", ""))
        beneficiary_bank_code = str(matched_beneficiary.get("bank_code", ""))
        current_account = str(acct_number) if acct_number else ""
        current_bank = str(bank_code) if bank_code else str(
            bank_name) if bank_name else ""

        # Only clear matched_beneficiary if it doesn't match current recipient AND we have complete recipient info
        if (current_account and current_bank and
                (beneficiary_account != current_account or beneficiary_bank_code != current_bank)):
            # Stale matched_beneficiary - clear it
            updates["matched_beneficiary"] = None
            debug_log(
                f"DEBUG find_beneficiary: Clearing stale matched_beneficiary (beneficiary: {beneficiary_account}/{beneficiary_bank_code} != current: {current_account}/{current_bank})")

    # PRESERVE partial recipient data: Don't clear account/bank if user is providing information incrementally
    # The state already has the correct values from extract_entities, so we just need to preserve them
    # Only update if we have new information to add

    # When we have both account and bank, clear llm_reply to prevent showing partial confirmations
    # The response will be set by validate_parallel or other nodes based on validation results
    # This prevents the entity extractor's "Got it. Sending to..." message from being shown
    # before we validate that the recipient is not the same as the source account
    if acct_number and (bank_code or bank_name):
        # Clear llm_reply - validation nodes will set appropriate response
        # But preserve all recipient data from state
        # Only clear llm_reply if we don't already have a response (to avoid losing important messages)
        result_state = {
            **state,
        }
        # Only clear llm_reply if response is empty (validation will set it)
        if not result_state.get("response"):
            result_state["llm_reply"] = None  # Clear to prevent partial confirmation messages
        if updates:
            result_state.update(updates)
        return cast(TransferState, result_state)

    # If we have partial data, preserve it and return state as-is (don't clear anything)
    # The extract_entities node has already handled preserving bank/account across messages
    if updates:
        result_state = {**state}
        result_state.update(updates)
        return cast(TransferState, result_state)

    return state

