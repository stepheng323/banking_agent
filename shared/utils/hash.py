"""
Generic security utilities for hashing and verification.

Uses bcrypt for secure password/PIN hashing via passlib.
bcrypt is designed to be computationally expensive to resist brute force attacks.
"""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _bcrypt_truncate(value: str) -> str:
    """Ensure plaintext fits bcrypt's 72-byte input limit.

    Bcrypt only considers the first 72 bytes of the input. We explicitly
    truncate to 72 bytes on UTF-8 boundaries to avoid ValueError from the
    underlying library and to make behavior explicit and consistent
    across hash and verify.
    """
    data = value.encode("utf-8")
    if len(data) <= 72:
        return value
    return data[:72].decode("utf-8", errors="ignore")


def hash_plaintext(value: str, algorithm: str = "bcrypt") -> str:  # noqa: ARG001
    """
    Hash a plaintext value using bcrypt.

    Args:
        value: The plaintext string to hash (e.g., PIN, password)
        algorithm: Ignored for backward compatibility, always uses bcrypt

    Returns:
        Bcrypt hash string (contains algorithm, cost, salt, and hash)
    """
    safe_value = _bcrypt_truncate(value)
    return pwd_context.hash(safe_value)


def verify_hash(value: str, stored_hash: str) -> bool:
    """
    Verify a plaintext value against a stored bcrypt hash.

    Args:
        value: The plaintext string to verify
        stored_hash: The stored bcrypt hash string

    Returns:
        True if the value matches the hash, False otherwise
    """
    try:
        safe_value = _bcrypt_truncate(value)
        return pwd_context.verify(safe_value, stored_hash)
    except (ValueError, TypeError):
        return False


def is_valid_pin_format(pin: str) -> bool:
    """A PIN is valid if it is a string of exactly 4 digits."""
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()
