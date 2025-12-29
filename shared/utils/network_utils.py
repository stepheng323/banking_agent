"""Network utilities for Nigerian mobile networks."""

# Nigerian mobile network prefixes (source: NCC)
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


def resolve_network_from_phone(phone: str) -> str | None:
    """
    Resolve network provider from Nigerian phone number prefix.

    Args:
        phone: Phone number in any format (+234..., 234..., 0...)

    Returns:
        Network name (MTN, AIRTEL, GLO, 9MOBILE) or None if unknown
    """
    # Normalize to local format
    cleaned = phone.strip()
    if cleaned.startswith("+234"):
        cleaned = "0" + cleaned[4:]
    elif cleaned.startswith("234"):
        cleaned = "0" + cleaned[3:]

    # Get prefix (first 4 digits)
    if len(cleaned) >= 4:
        prefix = cleaned[:4]
        return NETWORK_PREFIXES.get(prefix)

    return None


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to local Nigerian format (0XXX...).

    Args:
        phone: Phone number in any format

    Returns:
        Phone number in local format (0XXX...)
    """
    cleaned = phone.strip()
    if cleaned.startswith("+234"):
        return "0" + cleaned[4:]
    elif cleaned.startswith("234"):
        return "0" + cleaned[3:]
    return cleaned
