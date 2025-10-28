"""
Generic security utilities for hashing and verification.
"""

import hashlib
import secrets


def hash_plaintext(value: str, algorithm: str = "sha256") -> str:
    if algorithm not in hashlib.algorithms_available:
        raise ValueError(f"Unsupported algorithm: {algorithm}")

    salt = secrets.token_hex(16)

    hash_obj = hashlib.new(algorithm, (salt + value).encode("utf-8"))
    value_hash = hash_obj.hexdigest()

    return f"{algorithm}${salt}${value_hash}"


def verify_hash(value: str, stored_hash: str) -> bool:
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3:
            return False

        algorithm, salt, expected_hash = parts

        if algorithm not in hashlib.algorithms_available:
            return False

        hash_obj = hashlib.new(algorithm, (salt + value).encode("utf-8"))
        actual_hash = hash_obj.hexdigest()

        return actual_hash == expected_hash
    except (ValueError, TypeError):
        return False


def is_valid_pin_format(pin: str) -> bool:
    return pin and len(pin) == 4 and pin.isdigit()
