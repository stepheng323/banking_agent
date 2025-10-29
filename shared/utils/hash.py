"""
Generic security utilities for hashing and verification.

Uses bcrypt for secure password/PIN hashing via passlib.
bcrypt is designed to be computationally expensive to resist brute force attacks.
"""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_plaintext(value: str, algorithm: str = "bcrypt") -> str:
    """
    Hash a plaintext value using bcrypt.

    Args:
        value: The plaintext string to hash (e.g., PIN, password)
        algorithm: Ignored for backward compatibility, always uses bcrypt

    Returns:
        Bcrypt hash string (contains algorithm, cost, salt, and hash)
    """
    return pwd_context.hash(value)


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
        return pwd_context.verify(value, stored_hash)
    except (ValueError, TypeError):
        return False


def is_valid_pin_format(pin: str) -> bool:
    """A PIN is valid if it is a string of exactly 4 digits."""
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()
