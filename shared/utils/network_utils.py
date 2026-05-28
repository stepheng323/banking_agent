"""Network utilities for Nigerian mobile networks."""

import re

NETWORK_PREFIXES = {
    # MTN Nigeria
    "0803": "MTN",
    "0806": "MTN",
    "0813": "MTN",
    "0814": "MTN",
    "0816": "MTN",
    "0903": "MTN",
    "0906": "MTN",
    "0913": "MTN",
    "0703": "MTN",
    "0706": "MTN",
    # Airtel Nigeria
    "0802": "AIRTEL",
    "0808": "AIRTEL",
    "0812": "AIRTEL",
    "0701": "AIRTEL",
    "0902": "AIRTEL",
    "0901": "AIRTEL",
    "0904": "AIRTEL",
    "0907": "AIRTEL",
    # Glo Nigeria
    "0805": "GLO",
    "0807": "GLO",
    "0811": "GLO",
    "0815": "GLO",
    "0705": "GLO",
    "0905": "GLO",
    # 9mobile (formerly Etisalat)
    "0809": "9MOBILE",
    "0817": "9MOBILE",
    "0818": "9MOBILE",
    "0909": "9MOBILE",
    "0908": "9MOBILE",
}

NETWORK_ALIASES = {
    "mtn": "MTN",
    "airtel": "AIRTEL",
    "glo": "GLO",
    "globacom": "GLO",
    "glomobile": "GLO",
    "9mobile": "9MOBILE",
    "etisalat": "9MOBILE",
}


def _digits_only(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def normalize_nigerian_phone(phone: str) -> str | None:
    """Return canonical local Nigerian number (0XXXXXXXXXX) when valid."""
    digits = _digits_only(phone)
    if not digits:
        return None

    if len(digits) == 13 and digits.startswith("234"):
        digits = f"0{digits[3:]}"
    elif len(digits) == 10:
        digits = f"0{digits}"

    if len(digits) == 11 and digits.startswith("0"):
        return digits
    return None


def is_valid_nigerian_phone(phone: str) -> bool:
    """Return True when phone can be normalized to local Nigerian format."""
    return normalize_nigerian_phone(phone) is not None


def normalize_network_name(network: str | None) -> str | None:
    """Normalize network aliases to canonical provider names."""
    if not network:
        return None
    token = network.strip().lower()
    return NETWORK_ALIASES.get(token)


def format_network_display_name(network: str | None) -> str:
    """Return a user-facing network name while preserving canonical storage elsewhere."""
    canonical = normalize_network_name(network)
    if canonical == "MTN":
        return "MTN"
    if canonical == "AIRTEL":
        return "Airtel"
    if canonical == "GLO":
        return "Glo"
    if canonical == "9MOBILE":
        return "9mobile"
    return (network or "").strip()


def resolve_network_from_phone(phone: str) -> str | None:
    """
    Resolve network provider from Nigerian phone number prefix.

    Args:
        phone: Phone number in any format (+234..., 234..., 0..., spaced)

    Returns:
        Network name (MTN, AIRTEL, GLO, 9MOBILE) or None if unknown
    """
    normalized = normalize_nigerian_phone(phone)
    if not normalized:
        return None
    return NETWORK_PREFIXES.get(normalized[:4])


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to local Nigerian format (0XXXXXXXXXX) where possible.

    Falls back to digits-only content when normalization is not possible.
    """
    normalized = normalize_nigerian_phone(phone)
    if normalized:
        return normalized
    digits = _digits_only(phone)
    return digits if digits else phone.strip()
