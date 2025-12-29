"""Beneficiary matching node for airtime purchase flow."""

from typing import cast

from apps.core.src.agent.tools.response import (
    ResponseIntent,
    build_clarification_context,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from shared.database.models import Beneficiary
from shared.utils.serialization import sqlalchemy_to_dict

from ..utils import debug_log


async def find_beneficiary(
    state: AirtimeState,
    matcher: BeneficiaryMatcher,
) -> AirtimeState:
    """Find airtime beneficiary by name/alias (phone number and network)."""
    rec_name = state.get("recipient_name")
    recipient_phone = state.get("recipient_phone")
    network = state.get("network")
    beneficiaries = state.get("beneficiaries", [])
    synthesizer = get_synthesizer()

    # Filter beneficiaries to only airtime type
    from . import filter_airtime_beneficiaries

    airtime_beneficiaries = filter_airtime_beneficiaries(beneficiaries)

    if rec_name and not (recipient_phone and network):
        beneficiaries_models = [
            Beneficiary(**b) if isinstance(b, dict) else b for b in airtime_beneficiaries
        ]
        status, single, candidates = matcher.match(rec_name, beneficiaries_models)

        if status == "single" and single:
            return cast(
                AirtimeState,
                {
                    **state,
                    # account_number stores phone for airtime
                    "recipient_phone": str(single.account_number),
                    # bank_code stores network code for airtime
                    "network": str(single.bank_code),
                    "recipient_name": str(single.account_name or single.alias or rec_name),
                    "matched_beneficiary": sqlalchemy_to_dict(single)
                    if hasattr(single, "__table__")
                    else single,
                },
            )
        elif status == "clarify" and candidates:
            candidates_data = [
                {
                    "name": b.account_name or b.alias,
                    "network": b.bank_name,
                    "phone_number": str(b.account_number) if b.account_number else "",
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
            return cast(
                AirtimeState,
                {
                    **state,
                    "matched_beneficiary": None,
                    "flow_state": "collecting_phone",
                    "response": response,
                },
            )
        else:
            if recipient_phone and not network:
                context = build_response_context(ResponseIntent.ASK_NETWORK, state)
                response = await synthesizer.synthesize(context)
                # Prepend amount change acknowledgment if present
                ack = state.get("_amount_changed_ack", "")
                if ack:
                    response = f"{ack} {response}"
                return cast(
                    AirtimeState,
                    {
                        **state,
                        "matched_beneficiary": None,
                        "flow_state": "collecting_phone",
                        "response": response,
                        "_amount_changed_ack": None,  # Clear after use
                    },
                )
            else:
                context = build_response_context(ResponseIntent.ASK_PHONE_NUMBER, state)
                response = await synthesizer.synthesize(context)
                ack = state.get("_amount_changed_ack", "")
                if ack:
                    response = f"{ack} {response}"
                return cast(
                    AirtimeState,
                    {
                        **state,
                        "matched_beneficiary": None,
                        "flow_state": "collecting_phone",
                        "response": response,
                        "_amount_changed_ack": None,
                    },
                )

    if recipient_phone and not network:
        context = build_response_context(ResponseIntent.ASK_NETWORK, state)
        response = await synthesizer.synthesize(context)
        ack = state.get("_amount_changed_ack", "")
        if ack:
            response = f"{ack} {response}"
        return cast(
            AirtimeState,
            {
                **state,
                "matched_beneficiary": None,
                "flow_state": "collecting_phone",
                "response": response,
                "_amount_changed_ack": None,
            },
        )

    if network and not recipient_phone:
        context = build_response_context(ResponseIntent.ASK_PHONE_NUMBER, state)
        response = await synthesizer.synthesize(context)
        ack = state.get("_amount_changed_ack", "")
        if ack:
            response = f"{ack} {response}"
        return cast(
            AirtimeState,
            {
                **state,
                "matched_beneficiary": None,
                "flow_state": "collecting_phone",
                "response": response,
                "_amount_changed_ack": None,
            },
        )

    if not recipient_phone or not network:
        context = build_response_context(ResponseIntent.ASK_PHONE_NUMBER, state)
        response = await synthesizer.synthesize(context)
        ack = state.get("_amount_changed_ack", "")
        if ack:
            response = f"{ack} {response}"
        return cast(
            AirtimeState,
            {
                **state,
                "matched_beneficiary": None,
                "flow_state": "collecting_phone",
                "response": response,
                "_amount_changed_ack": None,
            },
        )

    matched_beneficiary = state.get("matched_beneficiary")
    updates = {}

    if matched_beneficiary and isinstance(matched_beneficiary, dict):
        beneficiary_phone = str(matched_beneficiary.get("account_number", ""))
        beneficiary_network = str(matched_beneficiary.get("bank_code", ""))
        current_phone = str(recipient_phone) if recipient_phone else ""
        current_network = str(network) if network else ""

        if (
            current_phone
            and current_network
            and (beneficiary_phone != current_phone or beneficiary_network != current_network)
        ):
            updates["matched_beneficiary"] = None
            debug_log(
                f"DEBUG find_beneficiary: Clearing stale matched_beneficiary (beneficiary: {beneficiary_phone}/{beneficiary_network} != current: {current_phone}/{current_network})"
            )

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
