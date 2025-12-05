"""Validation node for airtime purchase flow."""

import re
from typing import cast

from apps.core.src.agent.sub_agents.airtime.state import AirtimeState

from ..graph.utils import debug_log

MIN_AIRTIME_AMOUNT = 50.0
MAX_AIRTIME_AMOUNT = 50000.0  

NETWORK_PREFIXES = {
    "mtn": ["0803", "0806", "0703", "0706", "0813", "0816", "0810", "0814", "0903", "0906"],
    "airtel": ["0802", "0808", "0708", "0812", "0901", "0902", "0904", "0907"],
    "glo": ["0805", "0807", "0705", "0815", "0811", "0905"],
    "9mobile": ["0809", "0817", "0818", "0908", "0909"],
}

NETWORK_NAMES = {
    "mtn": "MTN",
    "airtel": "Airtel",
    "glo": "Glo",
    "9mobile": "9mobile",
    "etisalat": "9mobile",
}


def normalize_phone(phone: str) -> str | None:
    """
    Normalize Nigerian phone number to 11-digit format.
    
    Args:
        phone: Phone number in various formats
        
    Returns:
        Normalized 11-digit phone (08012345678) or None if invalid
    """
    if not phone:
        return None
    
    digits_only = re.sub(r"[^\d]", "", phone)
    
    if len(digits_only) == 13 and digits_only.startswith("234"):
        return "0" + digits_only[3:]
    
    if len(digits_only) == 11 and digits_only.startswith("0"):
        return digits_only
    
    if len(digits_only) == 10:
        return "0" + digits_only
    
    return None


def validate_phone_format(phone: str) -> bool:
    """Validate Nigerian phone number format."""
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    
    if len(normalized) != 11 or not normalized.startswith("0"):
        return False
    
    if normalized[1] not in ["7", "8", "9"]:
        return False
    
    return True


def get_network_from_phone(phone: str) -> str | None:
    """
    Determine network from phone number prefix.
    
    Args:
        phone: Normalized phone number (11 digits starting with 0)
        
    Returns:
        Network name (MTN, Airtel, Glo, 9mobile) or None if unknown
    """
    normalized = normalize_phone(phone)
    if not normalized or len(normalized) != 11:
        return None
    
    prefix = normalized[:4]
    
    for network, prefixes in NETWORK_PREFIXES.items():
        if prefix in prefixes:
            return NETWORK_NAMES[network]
    
    return None


def normalize_network(network: str) -> str | None:
    """
    Normalize network name to standard format.
    
    Args:
        network: Network name in various formats
        
    Returns:
        Standardized network name or None if invalid
    """
    if not network:
        return None
    
    network_lower = network.lower().strip()
    
    if network_lower in NETWORK_NAMES:
        return NETWORK_NAMES[network_lower]
    
    if "mtn" in network_lower:
        return "MTN"
    if "airtel" in network_lower:
        return "Airtel"
    if "glo" in network_lower:
        return "Glo"
    if "9mobile" in network_lower or "etisalat" in network_lower:
        return "9mobile"
    
    return None


async def validate_amount(state: AirtimeState) -> AirtimeState:
    """Validate that amount is present and within limits."""
    amount = state.get("amount")
    validation_errors = state.get("validation_errors", []).copy()
    
    if not amount:
        debug_log("validate_amount: Amount missing")
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "collecting_amount",
                "response": state.get("llm_reply") or "How much airtime would you like to purchase?",
                "validation_errors": validation_errors,
            },
        )
    
    try:
        amount_float = float(amount)
        
        if amount_float <= 0:
            validation_errors.append("Amount must be greater than zero")
            return cast(
                AirtimeState,
                {
                    **state,
                    "flow_state": "error",
                    "response": "Amount must be greater than zero. Please enter a valid amount.",
                    "validation_errors": validation_errors,
                },
            )
        
        if amount_float < MIN_AIRTIME_AMOUNT:
            validation_errors.append(f"Amount below minimum of ₦{MIN_AIRTIME_AMOUNT:,.0f}")
            return cast(
                AirtimeState,
                {
                    **state,
                    "flow_state": "error",
                    "response": f"Minimum airtime purchase is ₦{MIN_AIRTIME_AMOUNT:,.0f}. Please enter a higher amount.",
                    "validation_errors": validation_errors,
                },
            )
        
        if amount_float > MAX_AIRTIME_AMOUNT:
            validation_errors.append(f"Amount above maximum of ₦{MAX_AIRTIME_AMOUNT:,.0f}")
            return cast(
                AirtimeState,
                {
                    **state,
                    "flow_state": "error",
                    "response": f"Maximum airtime purchase is ₦{MAX_AIRTIME_AMOUNT:,.0f}. Please enter a lower amount.",
                    "validation_errors": validation_errors,
                },
            )
        
        debug_log(f"validate_amount: Amount valid - ₦{amount_float:,.2f}")
        return cast(
            AirtimeState,
            {
                **state,
                "amount": amount_float,
                "validation_errors": [],
            },
        )
    
    except (ValueError, TypeError):
        validation_errors.append("Invalid amount format")
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "error",
                "response": "Invalid amount format. Please enter a valid amount (e.g., 1000 or ₦1000).",
                "validation_errors": validation_errors,
            },
        )


async def validate_phone(state: AirtimeState) -> AirtimeState:
    """Validate that phone number is present and valid."""
    recipient_phone = state.get("recipient_phone")
    validation_errors = state.get("validation_errors", []).copy()
    
    if not recipient_phone:
        debug_log("validate_phone: Phone number missing")
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "collecting_phone",
                "response": state.get("llm_reply") or "Which phone number should I send the airtime to?",
                "validation_errors": validation_errors,
            },
        )
    
    normalized_phone = normalize_phone(recipient_phone)
    
    if not normalized_phone or not validate_phone_format(normalized_phone):
        validation_errors.append("Invalid phone number format")
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "error",
                "response": "Invalid phone number format. Please enter a valid Nigerian phone number (e.g., 08012345678).",
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
    
    if not network:
        debug_log("validate_network: Network missing")
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "collecting_phone",
                "response": state.get("llm_reply") or "Which network? (MTN, Airtel, Glo, or 9mobile)",
                "validation_errors": validation_errors,
            },
        )
    
    normalized_network = normalize_network(network)
    
    if not normalized_network:
        validation_errors.append("Invalid network name")
        return cast(
            AirtimeState,
            {
                **state,
                "flow_state": "error",
                "response": "Invalid network. Please choose from: MTN, Airtel, Glo, or 9mobile.",
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

