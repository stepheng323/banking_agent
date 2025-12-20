"""Validation node for airtime purchase flow."""

from typing import cast

from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from apps.core.src.agent.orchestrator.features.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from shared.utils.phone_utils import (
    normalize_phone,
    validate_phone_format,
    detect_network_from_phone as get_network_from_phone,
    normalize_network,
)

from ..graph.utils import debug_log

MIN_AIRTIME_AMOUNT = 50.0
MAX_AIRTIME_AMOUNT = 50000.0


async def validate_amount(state: AirtimeState) -> AirtimeState:
    """Validate that amount is present and within limits."""
    amount = state.get("amount")
    validation_errors = state.get("validation_errors", []).copy()
    synthesizer = get_synthesizer()
    
    if not amount:
        debug_log("validate_amount: Amount missing")
        context = build_response_context(ResponseIntent.ASK_AMOUNT, state)
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "collecting_amount",
                "response": response,
                "validation_errors": validation_errors,
            },
        )
    
    try:
        amount_float = float(amount)
        
        if amount_float <= 0:
            validation_errors.append("Amount must be greater than zero")
            context = build_response_context(
                ResponseIntent.INVALID_AMOUNT, 
                state,
                error_message="Amount must be greater than zero."
            )
            response = await synthesizer.synthesize(context)
            return cast(
                AirtimeState,
                {
                    **state,
                    "flow_state": "error",
                    "response": response,
                    "validation_errors": validation_errors,
                },
            )
        
        if amount_float < MIN_AIRTIME_AMOUNT:
            validation_errors.append(f"Amount below minimum of ₦{MIN_AIRTIME_AMOUNT:,.0f}")
            context = build_response_context(
                ResponseIntent.INVALID_AMOUNT,
                state,
                error_message=f"Minimum airtime is ₦{MIN_AIRTIME_AMOUNT:,.0f}."
            )
            response = await synthesizer.synthesize(context)
            return cast(
                AirtimeState,
                {
                    **state,
                    "flow_state": "error",
                    "response": response,
                    "validation_errors": validation_errors,
                },
            )
        
        if amount_float > MAX_AIRTIME_AMOUNT:
            validation_errors.append(f"Amount above maximum of ₦{MAX_AIRTIME_AMOUNT:,.0f}")
            context = build_response_context(
                ResponseIntent.INVALID_AMOUNT,
                state,
                error_message=f"Maximum airtime is ₦{MAX_AIRTIME_AMOUNT:,.0f}."
            )
            response = await synthesizer.synthesize(context)
            return cast(
                AirtimeState,
                {
                    **state,
                    "flow_state": "error",
                    "response": response,
                    "validation_errors": validation_errors,
                },
            )
        
        debug_log(f"validate_amount: Amount valid - ₦{amount_float:,.2f}")
        # Clear extracting state to prevent routing loop
        new_flow_state = state.get("flow_state")
        if new_flow_state == "extracting":
            new_flow_state = None  # Clear the extracting state
        return cast(
            AirtimeState,
            {
                **state,
                "amount": amount_float,
                "flow_state": new_flow_state,
                "validation_errors": [],
            },
        )
    
    except (ValueError, TypeError):
        validation_errors.append("Invalid amount format")
        context = build_response_context(
            ResponseIntent.INVALID_AMOUNT,
            state,
            error_message="Please enter a valid amount (e.g., 1000)."
        )
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "error",
                "response": response,
                "validation_errors": validation_errors,
            },
        )


async def validate_phone(state: AirtimeState) -> AirtimeState:
    """Validate that phone number is present and valid."""
    recipient_phone = state.get("recipient_phone")
    validation_errors = state.get("validation_errors", []).copy()
    synthesizer = get_synthesizer()
    
    if not recipient_phone:
        debug_log("validate_phone: Phone number missing")
        context = build_response_context(ResponseIntent.ASK_PHONE_NUMBER, state)
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "collecting_phone",
                "response": response,
                "validation_errors": validation_errors,
            },
        )
    
    normalized_phone = normalize_phone(recipient_phone)
    
    if not normalized_phone or not validate_phone_format(normalized_phone):
        validation_errors.append("Invalid phone number format")
        context = build_response_context(ResponseIntent.INVALID_PHONE_NUMBER, state)
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "error",
                "response": response,
                "validation_errors": validation_errors,
            },
        )
    
    debug_log(f"validate_phone: Phone number valid - {normalized_phone}")
    return cast(
        AirtimeState,
        {
            **state,
            "recipient_phone": normalized_phone,
            "validation_errors": [],
        },
    )


async def validate_network(state: AirtimeState) -> AirtimeState:
    """Validate that network is present and matches phone number."""
    network = state.get("network")
    recipient_phone = state.get("recipient_phone")
    validation_errors = state.get("validation_errors", []).copy()
    synthesizer = get_synthesizer()
    
    if not network:
        debug_log("validate_network: Network missing")
        context = build_response_context(ResponseIntent.ASK_NETWORK, state)
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "collecting_phone",
                "response": response,
                "validation_errors": validation_errors,
            },
        )
    
    normalized_network = normalize_network(network)
    
    if not normalized_network:
        validation_errors.append("Invalid network name")
        context = build_response_context(ResponseIntent.INVALID_NETWORK, state)
        response = await synthesizer.synthesize(context)
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "error",
                "response": response,
                "validation_errors": validation_errors,
            },
        )
    
    if recipient_phone:
        phone_network = get_network_from_phone(recipient_phone)
        if phone_network and phone_network != normalized_network:
            debug_log(
                f"validate_network: Warning - Phone prefix suggests {phone_network} but network is {normalized_network}"
            )
    
    debug_log(f"validate_network: Network valid - {normalized_network}")
    return cast(
        AirtimeState,
        {
            **state,
            "network": normalized_network,
            "validation_errors": [],
        },
    )
