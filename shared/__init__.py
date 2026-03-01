"""Shared library exports.

Keep package import lightweight so optional dependencies are loaded only when
their symbols are requested.
"""

from typing import TYPE_CHECKING, Any

__all__ = ["hash_plaintext", "verify_hash", "is_valid_pin_format"]

if TYPE_CHECKING:
    from shared.utils.hash import hash_plaintext, is_valid_pin_format, verify_hash


def __getattr__(name: str) -> Any:
    if name in {"hash_plaintext", "verify_hash", "is_valid_pin_format"}:
        from shared.utils.hash import hash_plaintext, is_valid_pin_format, verify_hash

        exports = {
            "hash_plaintext": hash_plaintext,
            "verify_hash": verify_hash,
            "is_valid_pin_format": is_valid_pin_format,
        }
        return exports[name]

    raise AttributeError(f"module 'shared' has no attribute '{name}'")
