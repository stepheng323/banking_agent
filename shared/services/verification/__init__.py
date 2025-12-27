"""Verification data service package."""

from shared.services.verification.service import (
    VERIFICATION_STORAGE_TTL,
    get_verification_data,
    set_verification_data,
    update_verification_data,
)

__all__ = [
    "get_verification_data",
    "set_verification_data",
    "update_verification_data",
    "VERIFICATION_STORAGE_TTL",
]
