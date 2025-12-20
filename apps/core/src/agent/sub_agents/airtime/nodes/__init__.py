"""Airtime purchase flow nodes."""

from typing import Any, List

from .extraction import extract_entities
from .validation import validate_amount, validate_phone, validate_network
from .account_selection import select_source_account
from .beneficiary import find_beneficiary
from .context import load_user_context
from .confirmation import prepare_confirmation
from .cancellation import handle_cancellation
from .authorization import authorize_transaction


def filter_airtime_beneficiaries(beneficiaries: List[Any]) -> List[Any]:
    """Filter beneficiaries to only include airtime type."""
    result = []
    for b in beneficiaries:
        if isinstance(b, dict):
            if b.get("beneficiary_type") == "airtime":
                result.append(b)
        elif hasattr(b, "beneficiary_type") and b.beneficiary_type == "airtime":
            result.append(b)
    return result

__all__ = [
    "extract_entities",
    "validate_amount",
    "validate_phone",
    "validate_network",
    "select_source_account",
    "find_beneficiary",
    "load_user_context",
    "prepare_confirmation",
    "handle_cancellation",
    "authorize_transaction",
]
