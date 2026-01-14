"""Airtime graph capability definitions.

Defines what the airtime graph supports and doesn't support.
Capability check happens after extraction, before validation.
"""

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.airtime.models import AirtimeExtractionResult


class AirtimeCapability(str, Enum):
    """Capabilities that can be required for airtime purchase."""

    SELF_RECHARGE = "self_recharge"
    OTHER_RECHARGE = "other_recharge"
    AMOUNT_VALID = "amount_valid"
    AMOUNT_OVER_LIMIT = "amount_over_limit"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"


AIRTIME_SUPPORTS: list[AirtimeCapability] = [
    AirtimeCapability.SELF_RECHARGE,
    AirtimeCapability.OTHER_RECHARGE,
    AirtimeCapability.AMOUNT_VALID,
]


AIRTIME_LIMITS = {
    "min_amount": 50,
    "max_amount": 50_000,
}


CAPABILITY_LABELS: dict[AirtimeCapability, str] = {
    AirtimeCapability.SELF_RECHARGE: "self recharge",
    AirtimeCapability.OTHER_RECHARGE: "recharge for others",
    AirtimeCapability.AMOUNT_VALID: "valid amount",
    AirtimeCapability.AMOUNT_OVER_LIMIT: "amounts over ₦50k",
    AirtimeCapability.SCHEDULED: "scheduled recharge",
    AirtimeCapability.RECURRING: "recurring recharge",
}


CAPABILITY_ALTERNATIVES: dict[AirtimeCapability, list[AirtimeCapability]] = {
    AirtimeCapability.AMOUNT_OVER_LIMIT: [AirtimeCapability.AMOUNT_VALID],
    AirtimeCapability.SCHEDULED: [],
    AirtimeCapability.RECURRING: [],
}


def check_capabilities(requires: list[AirtimeCapability]) -> list[AirtimeCapability]:
    """Check which required capabilities are missing."""
    return [cap for cap in requires if cap not in AIRTIME_SUPPORTS]


def get_alternatives(missing: list[AirtimeCapability]) -> list[AirtimeCapability]:
    """Get alternative capabilities for missing ones."""
    alternatives = []
    for cap in missing:
        alts = CAPABILITY_ALTERNATIVES.get(cap, [])
        alternatives.extend(alts)
    return alternatives


def derive_requirements(
    extraction: "AirtimeExtractionResult",
    user_message: str = "",
) -> list[AirtimeCapability]:
    """Derive required capabilities from extraction result."""
    requires: list[AirtimeCapability] = []

    if extraction.entities:
        if extraction.entities.is_self:
            requires.append(AirtimeCapability.SELF_RECHARGE)
        elif extraction.entities.recipient_phone:
            requires.append(AirtimeCapability.OTHER_RECHARGE)

        if extraction.entities.amount:
            if extraction.entities.amount > AIRTIME_LIMITS["max_amount"]:
                requires.append(AirtimeCapability.AMOUNT_OVER_LIMIT)
            elif extraction.entities.amount < AIRTIME_LIMITS["min_amount"]:
                pass  # Will be handled by validation
            else:
                requires.append(AirtimeCapability.AMOUNT_VALID)

    msg_lower = user_message.lower()

    schedule_keywords = ["tomorrow", "later", "schedule", "next"]
    if any(kw in msg_lower for kw in schedule_keywords):
        requires.append(AirtimeCapability.SCHEDULED)

    recurring_keywords = ["weekly", "monthly", "every week", "every month", "recurring", "automatically"]
    if any(kw in msg_lower for kw in recurring_keywords):
        requires.append(AirtimeCapability.RECURRING)

    return list(set(requires))


def generate_limitation_message(missing: list[AirtimeCapability]) -> str:
    """Generate user-friendly limitation message."""
    if not missing:
        return ""

    if AirtimeCapability.AMOUNT_OVER_LIMIT in missing:
        return (
            f"The maximum airtime purchase is *₦{AIRTIME_LIMITS['max_amount']:,}* per transaction.\n\n"
            "Would you like to proceed with a smaller amount?"
        )

    if AirtimeCapability.SCHEDULED in missing:
        return (
            "Scheduled airtime purchases aren't available yet.\n\n"
            "Want me to proceed with an immediate recharge?"
        )

    if AirtimeCapability.RECURRING in missing:
        return (
            "Recurring/automatic airtime purchases aren't available yet.\n\n"
            "I can do a one-time recharge now. Proceed?"
        )

    missing_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in missing]
    return f"*{missing_labels[0].title()}* isn't available yet."
