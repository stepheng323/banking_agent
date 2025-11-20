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
                "recipient_account": str(single.account_number) if single.account_number else "",
                "recipient_bank_code": str(single.bank_code) if single.bank_code else "",
                "recipient_bank_name": str(single.bank_name) if single.bank_name else "",
                "recipient_name": str(single.account_name or single.alias or rec_name),
                "matched_beneficiary": sqlalchemy_to_dict(single) if hasattr(single, "__table__") else single,
            }
        elif status == "clarify" and candidates:
            opts = "; ".join([
                f"{(b.account_name or b.alias)} ({b.bank_name or 'N/A'} • {str(b.account_number)[-4:] if b.account_number else 'N/A'})"
                for b in candidates
            ])
            return {
                **state,
                "matched_beneficiary": None,
                "flow_state": "collecting_recipient",
                "response": f"I found multiple matches for '{rec_name}'. Which one? {opts}",
            }
        else:
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
                    "matched_beneficiary": None,
                    "flow_state": "collecting_recipient",
                    "response": "Please provide the account number and bank name.",
                }

    if acct_number and not (bank_code or bank_name):
        return {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_recipient",
            "response": "Which bank is that for?",
        }

    if (bank_code or bank_name) and not acct_number:
        return {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_recipient",
            "response": "What is the account number?",
        }

    if not acct_number or not (bank_code or bank_name):
        return {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_recipient",
            "response": "Please provide the account number and bank name.",
        }

    matched_beneficiary = state.get("matched_beneficiary")
    updates = {}

    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        beneficiary_account = str(
            matched_beneficiary.get("account_number", ""))
        beneficiary_bank_code = str(matched_beneficiary.get("bank_code", ""))
        current_account = str(acct_number) if acct_number else ""
        current_bank = str(bank_code) if bank_code else str(
            bank_name) if bank_name else ""

        if (current_account and current_bank and
                (beneficiary_account != current_account or beneficiary_bank_code != current_bank)):
            updates["matched_beneficiary"] = None
            debug_log(
                f"DEBUG find_beneficiary: Clearing stale matched_beneficiary (beneficiary: {beneficiary_account}/{beneficiary_bank_code} != current: {current_account}/{current_bank})")

    if acct_number and (bank_code or bank_name):
        result_state = {
            **state,
        }
        if not result_state.get("response"):
            result_state["llm_reply"] = None
        if updates:
            result_state.update(updates)
        return cast(TransferState, result_state)
    if updates:
        result_state = {**state}
        result_state.update(updates)
        return cast(TransferState, result_state)

    return state
