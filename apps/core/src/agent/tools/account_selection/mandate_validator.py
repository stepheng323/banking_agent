"""Shared mandate status validation for all transaction flows."""

from typing import Dict, Tuple, Optional

from shared.config.settings import settings


MANDATE_STATUS_MESSAGES: Dict[str, str] = {
    "pending": "Your account authorization is pending. Please complete the ₦50 transfer to activate your account.",
    "initiated": "Your account authorization is pending. Please complete the ₦50 transfer to activate your account.",
    "approved": "Your account is almost ready. It will be fully active within 24 hours after your authorization transfer.",
    "paused": "Your account has been temporarily paused. Please contact support to reinstate it.",
    "rejected": "Your account authorization was rejected. Please contact support to resolve this.",
    "cancelled": "Your account authorization was cancelled. Please restart the account setup process.",
}


def validate_mandate_status(account: Dict) -> Tuple[bool, Optional[str]]:
    """
    Validate that an account's mandate status allows transactions.
    
    Args:
        account: Account dict with mandate_status field
        
    Returns:
        Tuple of (is_valid, error_message).
        - is_valid: True if account can transact, False otherwise
        - error_message: Human-readable error message if not valid, None if valid
    """
    if settings.app_env == "development":
        if account.get("_mock_mandate_ready"):
            return (True, None)
    
    mandate_status = account.get("mandate_status", "pending")
    
    if mandate_status == "ready":
        return (True, None)
    
    error_message = MANDATE_STATUS_MESSAGES.get(
        mandate_status,
        "Your account is not ready for payments yet. Please complete the authorization process."
    )
    
    return (False, error_message)
