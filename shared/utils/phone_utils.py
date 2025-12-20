"""Shared phone number utilities for airtime and transfer flows."""

import re
from typing import Optional


# Network prefix mappings
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


def normalize_phone(phone: str) -> Optional[str]:
    """
    Normalize Nigerian phone number to 11-digit format.
    
    Handles formats:
    - 08012345678 (11 digits)
    - 8012345678 (10 digits)
    - 2348012345678 (13 digits with country code)
    - +234 801 234 5678 (with spaces/dashes)
    
    Returns:
        Normalized 11-digit phone (08012345678) or None if invalid
    """
    if not phone:
        return None
    
    # Remove all non-digit characters
    digits_only = re.sub(r"[^\d]", "", phone)
    
    # Handle 13-digit format with country code 234
    if len(digits_only) == 13 and digits_only.startswith("234"):
        return "0" + digits_only[3:]
    
    # Already in correct 11-digit format
    if len(digits_only) == 11 and digits_only.startswith("0"):
        return digits_only
    
    # 10-digit format missing leading zero
    if len(digits_only) == 10:
        return "0" + digits_only
    
    return None


def validate_phone_format(phone: str) -> bool:
    """
    Validate Nigerian phone number format.
    
    Validates:
    - 11 digits starting with 0
    - Second digit is 7, 8, or 9 (mobile prefixes)
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    
    if len(normalized) != 11 or not normalized.startswith("0"):
        return False
    
    if normalized[1] not in ["7", "8", "9"]:
        return False
    
    return True


def detect_network_from_phone(phone: str) -> Optional[str]:
    """
    Detect network from phone number prefix.
    
    Args:
        phone: Phone number in any format
        
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


def normalize_network(network: str) -> Optional[str]:
    """
    Normalize network name to standard format.
    
    Handles variations like:
    - "mtn", "MTN", "Mtn" -> "MTN"
    - "airtel", "AIRTEL" -> "Airtel"
    - "etisalat", "9mobile" -> "9mobile"
    """
    if not network:
        return None
    
    network_lower = network.lower().strip()
    
    # Direct match
    if network_lower in NETWORK_NAMES:
        return NETWORK_NAMES[network_lower]
    
    # Partial match
    if "mtn" in network_lower:
        return "MTN"
    if "airtel" in network_lower:
        return "Airtel"
    if "glo" in network_lower:
        return "Glo"
    if "9mobile" in network_lower or "etisalat" in network_lower:
        return "9mobile"
    
    return None
