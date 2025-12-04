"""Beneficiary matching node for airtime purchase flow."""

from typing import cast

from shared.database.models import Beneficiary
from shared.utils.serialization import sqlalchemy_to_dict

from apps.core.src.agent.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def find_beneficiary(
    state: AirtimeState,
    matcher: BeneficiaryMatcher,
) -> AirtimeState:
    """Find airtime beneficiary by name/alias (phone number and network)."""
    rec_name = state.get("recipient_name")
    recipient_phone = state.get("recipient_phone")
    network = state.get("network")
    beneficiaries = state.get("beneficiaries", [])

    # Filter beneficiaries to only airtime type
    airtime_beneficiaries = []
    for b in beneficiaries:
        if isinstance(b, dict):
            if b.get("beneficiary_type") == "airtime":
                airtime_beneficiaries.append(b)
        elif hasattr(b, "beneficiary_type") and b.beneficiary_type == "airtime":
            airtime_beneficiaries.append(b)

    if rec_name and not (recipient_phone and network):
        beneficiaries_models = [
            Beneficiary(**b) if isinstance(b, dict) else b
            for b in airtime_beneficiaries
        ]
        status, single, candidates = matcher.match(
            rec_name, beneficiaries_models)

        if status == "single" and single:
            return cast(AirtimeState, {
                **state,
                # account_number stores phone for airtime
                "recipient_phone": str(single.account_number),
                # bank_code stores network code for airtime
                "network": str(single.bank_code),
                "recipient_name": str(single.account_name or single.alias or rec_name),
                "matched_beneficiary": sqlalchemy_to_dict(single) if hasattr(single, "__table__") else single,
            })
        elif status == "clarify" and candidates:
            opts = "; ".join([
                f"{(b.account_name or b.alias)} ({b.bank_name} • {str(b.account_number)[-4:]})"
                for b in candidates
            ])
            return cast(AirtimeState, {
                **state,
                "matched_beneficiary": None,
                "flow_state": "collecting_phone",
                "response": f"I found multiple matches for '{rec_name}'. Which one? {opts}",
            })
        else:
            if recipient_phone and not network:
                return cast(AirtimeState, {
                    **state,
                    "matched_beneficiary": None,
                    "flow_state": "collecting_phone",
                    "response": "Which network is that for? (MTN, Airtel, Glo, or 9mobile)",
                })
            else:
                return cast(AirtimeState, {
                    **state,
                    "matched_beneficiary": None,
                    "flow_state": "collecting_phone",
                    "response": "Please provide the phone number and network (MTN, Airtel, Glo, or 9mobile).",
                })

    if recipient_phone and not network:
        return cast(AirtimeState, {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_phone",
            "response": "Which network is that for? (MTN, Airtel, Glo, or 9mobile)",
        })

    if network and not recipient_phone:
        return cast(AirtimeState, {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_phone",
            "response": "What is the phone number?",
        })

    if not recipient_phone or not network:
        return cast(AirtimeState, {
            **state,
            "matched_beneficiary": None,
            "flow_state": "collecting_phone",
            "response": "Please provide the phone number and network (MTN, Airtel, Glo, or 9mobile).",
        })

    matched_beneficiary = state.get("matched_beneficiary")
    updates = {}

    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        beneficiary_phone = str(matched_beneficiary.get("account_number", ""))
        beneficiary_network = str(matched_beneficiary.get("bank_code", ""))
        current_phone = str(recipient_phone) if recipient_phone else ""
        current_network = str(network) if network else ""

        if (current_phone and current_network and
                (beneficiary_phone != current_phone or beneficiary_network != current_network)):
            updates["matched_beneficiary"] = None
            debug_log(
                f"DEBUG find_beneficiary: Clearing stale matched_beneficiary (beneficiary: {beneficiary_phone}/{beneficiary_network} != current: {current_phone}/{current_network})")

    if recipient_phone and network:
        result_state = {
            **state,
        }
        if not result_state.get("response"):
            result_state["llm_reply"] = None
        if updates:
            result_state.update(updates)
        return cast(AirtimeState, result_state)
    if updates:
        result_state = {**state}
        result_state.update(updates)
        return cast(AirtimeState, result_state)

    return state
