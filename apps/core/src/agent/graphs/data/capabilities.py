"""Data graph capability definitions.

Defines what the data purchase graph supports and doesn't support.
Capability check happens after extraction, before plan selection.
"""

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.data.models_extraction import DataExtractionResult


class DataCapability(str, Enum):
    """Capabilities that can be required for data purchase."""

    SELF_PURCHASE = "self_purchase"
    OTHER_PURCHASE = "other_purchase"
    BUDGET_VALID = "budget_valid"
    BUDGET_OVER_LIMIT = "budget_over_limit"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"


DATA_SUPPORTS: list[DataCapability] = [
    DataCapability.SELF_PURCHASE,
    DataCapability.OTHER_PURCHASE,
    DataCapability.BUDGET_VALID,
]


DATA_LIMITS = {
    "min_budget": 50,
    "max_budget": 50_000,
}


CAPABILITY_LABELS: dict[DataCapability, str] = {
    DataCapability.SELF_PURCHASE: "data for your line",
    DataCapability.OTHER_PURCHASE: "data for others",
    DataCapability.BUDGET_VALID: "valid budget",
    DataCapability.BUDGET_OVER_LIMIT: "budgets over ₦50k",
    DataCapability.SCHEDULED: "scheduled data purchase",
    DataCapability.RECURRING: "recurring data purchase",
}


CAPABILITY_ALTERNATIVES: dict[DataCapability, list[DataCapability]] = {
    DataCapability.BUDGET_OVER_LIMIT: [DataCapability.BUDGET_VALID],
    DataCapability.SCHEDULED: [],
    DataCapability.RECURRING: [],
}


def check_capabilities(requires: list[DataCapability]) -> list[DataCapability]:
    """Check which required capabilities are missing."""
    return [cap for cap in requires if cap not in DATA_SUPPORTS]


def get_alternatives(missing: list[DataCapability]) -> list[DataCapability]:
    """Get alternative capabilities for missing ones."""
    alternatives = []
    for cap in missing:
        alts = CAPABILITY_ALTERNATIVES.get(cap, [])
        alternatives.extend(alts)
    return alternatives


def derive_requirements(
    extraction: "DataExtractionResult",
    user_message: str = "",
) -> list[DataCapability]:
    """Derive required capabilities from extraction result."""
    requires: list[DataCapability] = []

    if extraction.entities:
        if extraction.entities.is_self:
            requires.append(DataCapability.SELF_PURCHASE)
        elif extraction.entities.recipient_phone:
            requires.append(DataCapability.OTHER_PURCHASE)

        if extraction.entities.budget:
            if extraction.entities.budget > DATA_LIMITS["max_budget"]:
                requires.append(DataCapability.BUDGET_OVER_LIMIT)
            elif extraction.entities.budget >= DATA_LIMITS["min_budget"]:
                requires.append(DataCapability.BUDGET_VALID)

    msg_lower = user_message.lower()

    schedule_keywords = ["tomorrow", "later", "schedule", "next"]
    if any(kw in msg_lower for kw in schedule_keywords):
        requires.append(DataCapability.SCHEDULED)

    recurring_keywords = ["weekly", "monthly", "every week", "every month", "recurring", "automatically"]
    if any(kw in msg_lower for kw in recurring_keywords):
        requires.append(DataCapability.RECURRING)

    return list(set(requires))


def generate_limitation_message(missing: list[DataCapability]) -> str:
    """Generate user-friendly limitation message."""
    if not missing:
        return ""

    if DataCapability.BUDGET_OVER_LIMIT in missing:
        return (
            f"The maximum data purchase is *₦{DATA_LIMITS['max_budget']:,}* per transaction.\n\n"
            "Would you like to proceed with a smaller amount?"
        )

    if DataCapability.SCHEDULED in missing:
        return (
            "Scheduled data purchases aren't available yet.\n\n"
            "Want me to proceed with an immediate purchase?"
        )

    if DataCapability.RECURRING in missing:
        return (
            "Recurring/automatic data purchases aren't available yet.\n\n"
            "I can do a one-time purchase now. Proceed?"
        )

    missing_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in missing]
    return f"*{missing_labels[0].title()}* isn't available yet."
