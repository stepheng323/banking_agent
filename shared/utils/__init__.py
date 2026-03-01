"""Shared utilities package.

Avoid eager imports so optional runtime dependencies are loaded only when
needed by the calling code path.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "hash_plaintext",
    "verify_hash",
    "is_valid_pin_format",
    "decrypt_flow_data",
    "is_encrypted",
    "encrypt_flow_response",
]

if TYPE_CHECKING:
    from .flow_decryption import decrypt_flow_data, is_encrypted
    from .flow_encryption import encrypt_flow_response
    from .hash import hash_plaintext, is_valid_pin_format, verify_hash


def __getattr__(name: str) -> Any:
    if name in {"hash_plaintext", "verify_hash", "is_valid_pin_format"}:
        from .hash import hash_plaintext, is_valid_pin_format, verify_hash

        exports = {
            "hash_plaintext": hash_plaintext,
            "verify_hash": verify_hash,
            "is_valid_pin_format": is_valid_pin_format,
        }
        return exports[name]

    if name in {"decrypt_flow_data", "is_encrypted"}:
        from .flow_decryption import decrypt_flow_data, is_encrypted

        exports = {
            "decrypt_flow_data": decrypt_flow_data,
            "is_encrypted": is_encrypted,
        }
        return exports[name]

    if name == "encrypt_flow_response":
        from .flow_encryption import encrypt_flow_response

        return encrypt_flow_response

    raise AttributeError(f"module 'shared.utils' has no attribute '{name}'")
