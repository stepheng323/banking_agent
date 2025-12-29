"""Beneficiary matching node for transfer flow."""

from typing import cast

from apps.core.src.agent.tools.response import (
    ResponseIntent,
    build_clarification_context,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from shared.database.models import Beneficiary
from shared.utils.logging import get_logger
from shared.utils.serialization import sqlalchemy_to_dict

logger = get_logger(__name__)


async def find_beneficiary(
    state: TransferState,
    matcher: BeneficiaryMatcher,
) -> TransferState:
    """Find beneficiary by name (optional convenience feature). Account resolution happens via banking API."""
    rec_name = state.get("recipient_name")
    # Title case recipient name for better presentation
    if rec_name:
        rec_name = rec_name.strip().title()

    acct_number = state.get("recipient_account")
    bank_code = state.get("recipient_bank_code")
    bank_name = state.get("recipient_bank_name")
    beneficiaries = state.get("beneficiaries", [])

    synthesizer = get_synthesizer()

    # If account and bank are already provided, skip beneficiary matching
    if acct_number and (bank_code or bank_name):
        return state

    if rec_name and not (acct_number and (bank_code or bank_name)):
        beneficiaries_models = [
            Beneficiary(**b) if isinstance(b, dict) else b for b in beneficiaries
        ]
        status, single, candidates = matcher.match(rec_name, beneficiaries_models)

        if status == "single" and single:
            return {
                **state,
                "recipient_account": str(single.account_number) if single.account_number else "",
                "recipient_bank_code": str(single.bank_code) if single.bank_code else "",
                "recipient_bank_name": str(single.bank_name) if single.bank_name else "",
                "recipient_name": str(single.account_name or single.alias or rec_name),
                "matched_beneficiary": sqlalchemy_to_dict(single)
                if hasattr(single, "__table__")
                else single,
            }
        elif status == "clarify" and candidates:
            # Build candidates list for clarification
            candidates_data = [
                {
                    "name": b.account_name or b.alias,
                    "bank_name": b.bank_name or "N/A",
                    "account_number": str(b.account_number) if b.account_number else "",
                }
                for b in candidates
            ]
            context = build_clarification_context(
                ResponseIntent.CLARIFY_BENEFICIARY,
                candidates_data,
                state,
                recipient_name=rec_name,
            )
            response = await synthesizer.synthesize(context)
            return {
                **state,
                "matched_beneficiary": None,
                "flow_state": "collecting_recipient",
                "response": response,
            }
        else:
            if acct_number and not (bank_code or bank_name):
                context = build_response_context(
                    ResponseIntent.ASK_BANK, state, recipient_name=rec_name
                )
                response = await synthesizer.synthesize(context)
                return {
                    **state,
                    "matched_beneficiary": None,
                    "flow_state": "collecting_recipient",
                    "response": response,
                }
            else:
                context = build_response_context(
                    ResponseIntent.ASK_RECIPIENT, state, recipient_name=rec_name
                )
                response = await synthesizer.synthesize(context)
                return {
                    **state,
                    "matched_beneficiary": None,
                    "flow_state": "collecting_recipient",
                    "response": response,
                }

    if acct_number and not (bank_code or bank_name):
        context = build_response_context(ResponseIntent.ASK_BANK, state, recipient_name=rec_name)
        response = await synthesizer.synthesize(context)
        return {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_recipient",
            "response": response,
        }

    if (bank_code or bank_name) and not acct_number:
        context = build_response_context(
            ResponseIntent.ASK_ACCOUNT_NUMBER, state, recipient_name=rec_name
        )
        response = await synthesizer.synthesize(context)
        return {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_recipient",
            "response": response,
        }

    if not acct_number or not (bank_code or bank_name):
        context = build_response_context(
            ResponseIntent.ASK_RECIPIENT, state, recipient_name=rec_name
        )
        response = await synthesizer.synthesize(context)
        return {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_recipient",
            "response": response,
        }

    matched_beneficiary = state.get("matched_beneficiary")
    updates = {}

    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        beneficiary_account = str(matched_beneficiary.get("account_number", ""))
        beneficiary_bank_code = str(matched_beneficiary.get("bank_code", ""))
        current_account = str(acct_number) if acct_number else ""
        current_bank = str(bank_code) if bank_code else str(bank_name) if bank_name else ""

        if (
            current_account
            and current_bank
            and (beneficiary_account != current_account or beneficiary_bank_code != current_bank)
        ):
            updates["matched_beneficiary"] = None
            logger.debug(
                "Clearing stale matched_beneficiary",
                beneficiary_account=beneficiary_account,
                beneficiary_bank=beneficiary_bank_code,
                current_account=current_account,
                current_bank=current_bank,
            )

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
