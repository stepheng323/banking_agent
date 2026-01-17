"""Transfer graph capability definitions.

Defines what the transfer graph supports and doesn't support.
Capability check happens after extraction, before validation.
"""

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.transfer.models_extraction import TransferExtractionResult


class TransferCapability(str, Enum):
    """Capabilities that can be required by a transfer."""

    SINGLE_TRANSFER = "single_transfer"
    AMOUNT_UP_TO_10M = "amount_up_to_10m"
    AMOUNT_OVER_10M = "amount_over_10m"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"
    INTERNATIONAL = "international"


TRANSFER_SUPPORTS: list[TransferCapability] = [
    TransferCapability.SINGLE_TRANSFER,
    TransferCapability.AMOUNT_UP_TO_10M,
]


TRANSFER_LIMITS = {
    "max_amount": 10_000_000,
}


CAPABILITY_LABELS: dict[TransferCapability, str] = {
    TransferCapability.SINGLE_TRANSFER: "single transfer",
    TransferCapability.AMOUNT_UP_TO_10M: "transfers up to ₦10M",
    TransferCapability.AMOUNT_OVER_10M: "transfers over ₦10M",
    TransferCapability.SCHEDULED: "scheduled transfers",
    TransferCapability.RECURRING: "recurring transfers",
    TransferCapability.INTERNATIONAL: "international transfers",
}


CAPABILITY_ALTERNATIVES: dict[TransferCapability, list[TransferCapability]] = {
    TransferCapability.AMOUNT_OVER_10M: [TransferCapability.AMOUNT_UP_TO_10M],
    TransferCapability.SCHEDULED: [],
    TransferCapability.RECURRING: [],
    TransferCapability.INTERNATIONAL: [],
}


def check_capabilities(requires: list[TransferCapability]) -> list[TransferCapability]:
    """Check which required capabilities are missing."""
    return [cap for cap in requires if cap not in TRANSFER_SUPPORTS]


def get_alternatives(missing: list[TransferCapability]) -> list[TransferCapability]:
    """Get alternative capabilities for missing ones."""
    alternatives = []
    for cap in missing:
        alts = CAPABILITY_ALTERNATIVES.get(cap, [])
        alternatives.extend(alts)
    return alternatives


def derive_requirements(
    extraction: "TransferExtractionResult",
    user_message: str = "",
) -> list[TransferCapability]:
    """
    Derive required capabilities from extraction result.

    Args:
        extraction: The extraction result from LLM
        user_message: Original user message for detecting scheduling/recurring
    """
    requires: list[TransferCapability] = [TransferCapability.SINGLE_TRANSFER]

    if extraction.entities and extraction.entities.amount:
        if extraction.entities.amount > TRANSFER_LIMITS["max_amount"]:
            requires.append(TransferCapability.AMOUNT_OVER_10M)
        else:
            requires.append(TransferCapability.AMOUNT_UP_TO_10M)

    msg_lower = user_message.lower()

    schedule_keywords = ["tomorrow", "next week", "later", "schedule", "on friday", "on monday"]
    if any(kw in msg_lower for kw in schedule_keywords):
        requires.append(TransferCapability.SCHEDULED)

    recurring_keywords = ["weekly", "monthly", "every week", "every month", "recurring", "automatically"]
    if any(kw in msg_lower for kw in recurring_keywords):
        requires.append(TransferCapability.RECURRING)

    intl_keywords = ["international", "abroad", "foreign", "usa", "uk", "ghana", "overseas"]
    if any(kw in msg_lower for kw in intl_keywords):
        requires.append(TransferCapability.INTERNATIONAL)
        
    # Also check requested_features from LLM extraction
    for feature in extraction.requested_features:
        if feature == "SCHEDULED":
            requires.append(TransferCapability.SCHEDULED)
        elif feature == "RECURRING":
            requires.append(TransferCapability.RECURRING)
        elif feature == "INTERNATIONAL":
            requires.append(TransferCapability.INTERNATIONAL)

    return list(set(requires))


def generate_limitation_message(missing: list[TransferCapability]) -> str:
    """Generate user-friendly limitation message."""
    if not missing:
        return ""

    alternatives = get_alternatives(missing)
    missing_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in missing]
    alt_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in alternatives]

    msg = f"Got it — you want *{missing_labels[0]}*.\n\n"
    msg += "This isn't available yet."

    if TransferCapability.AMOUNT_OVER_10M in missing:
        msg = f"The amount exceeds the *₦10M limit* per transfer.\n\n"
        msg += "Would you like to split this into multiple transfers?"
    elif TransferCapability.SCHEDULED in missing:
        msg = "Scheduled transfers aren't available yet.\n\n"
        msg += "Want me to proceed with an immediate transfer?"
    elif TransferCapability.RECURRING in missing:
        msg = "Recurring transfers aren't available yet.\n\n"
        msg += "I can do a one-time transfer now. Proceed?"
    elif TransferCapability.INTERNATIONAL in missing:
        msg = "International transfers aren't available yet.\n\n"
        msg += "I can only transfer to Nigerian banks for now."
    elif alt_labels:
        msg += f" I can do *{alt_labels[0]}* instead.\n\nWant me to proceed?"

    return msg
