"""Data capability definitions.

Defines what data purchase features are supported and provides capability negotiation.
Moved to shared layer to break circular imports.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DataCapability(str, Enum):
    """Features that can be required for data purchase."""

    SELF_PURCHASE = "self_purchase"
    OTHER_PURCHASE = "other_purchase"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"


DATA_SUPPORTS: set[DataCapability] = {
    DataCapability.SELF_PURCHASE,
    DataCapability.OTHER_PURCHASE,
}


CAPABILITY_LABELS: dict[DataCapability, str] = {
    DataCapability.SELF_PURCHASE: "data for your line",
    DataCapability.OTHER_PURCHASE: "data for others",
    DataCapability.SCHEDULED: "scheduled data purchase",
    DataCapability.RECURRING: "recurring data purchase",
}


@dataclass
class CapabilityDecision:
    """Structured decision from capability check."""

    allowed: bool
    missing: list[DataCapability] = field(default_factory=list)
    prompt: str = ""
    suggested_action: str | None = None
    patch: dict[str, Any] = field(default_factory=dict)


def derive_requirements(
    user_message: str = "",
    requested_features: list[str] | None = None,
    is_self: bool = False,
) -> list[DataCapability]:
    """Derive required capabilities from user message."""
    requires: set[DataCapability] = set()

    if is_self:
        requires.add(DataCapability.SELF_PURCHASE)
    else:
        requires.add(DataCapability.OTHER_PURCHASE)

    requested = set(requested_features or [])
    msg = user_message.lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in msg for k in keywords)

    if "SCHEDULED" in requested or has_any(["schedule", "tomorrow", "later", "next"]):
        requires.add(DataCapability.SCHEDULED)

    if "RECURRING" in requested or has_any(["every week", "weekly", "monthly", "recurring", "automatically"]):
        requires.add(DataCapability.RECURRING)

    return list(requires)


def decide_capability(requires: list[DataCapability]) -> CapabilityDecision:
    """Check capabilities and return structured decision."""
    missing = [cap for cap in requires if cap not in DATA_SUPPORTS]

    if not missing:
        return CapabilityDecision(allowed=True)

    if DataCapability.SCHEDULED in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[DataCapability.SCHEDULED],
            suggested_action="proceed_immediate",
            patch={"scheduled": False, "schedule_date": None},
            prompt="Scheduled data purchases aren't available yet.\n\nWant me to proceed with an immediate purchase?",
        )

    if DataCapability.RECURRING in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[DataCapability.RECURRING],
            suggested_action="one_time",
            patch={"recurring": False, "recurring_schedule": None},
            prompt="Recurring data purchases aren't available yet.\n\nI can do a one-time purchase now. Proceed?",
        )

    label = CAPABILITY_LABELS.get(missing[0], missing[0].value)
    return CapabilityDecision(
        allowed=False,
        missing=[missing[0]],
        prompt=f"I get you — you want *{label}*.\n\nThat isn't available yet.",
    )
