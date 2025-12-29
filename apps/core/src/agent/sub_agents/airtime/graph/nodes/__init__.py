"""Airtime purchase flow nodes."""

from typing import Any

from .account_selection import select_source_account
from .authorization import authorize_transaction
from .beneficiary import find_beneficiary
from .cancellation import handle_cancellation
from .confirmation import prepare_confirmation
from .context import load_user_context
from .extraction import extract_entities
from .validation import validate_amount, validate_network, validate_phone


def filter_airtime_beneficiaries(beneficiaries: list[Any]) -> list[Any]:
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
