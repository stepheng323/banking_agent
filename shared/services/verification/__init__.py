"""Verification data service package."""
from shared.services.verification.service import (
    get_verification_data,
    set_verification_data,
    update_verification_data,
    VERIFICATION_STORAGE_TTL,
)

__all__ = [
    "get_verification_data",
    "set_verification_data",
    "update_verification_data",
    "VERIFICATION_STORAGE_TTL",
]
