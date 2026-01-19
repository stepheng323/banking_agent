"""Airtime capability definitions.

Defines what airtime features are supported and provides capability negotiation.
Moved to shared layer to break circular imports.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AirtimeCapability(str, Enum):
    """Features that can be required for airtime purchase."""

    SELF_RECHARGE = "self_recharge"
    OTHER_RECHARGE = "other_recharge"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"


AIRTIME_SUPPORTS: set[AirtimeCapability] = {
    AirtimeCapability.SELF_RECHARGE,
    AirtimeCapability.OTHER_RECHARGE,
}


CAPABILITY_LABELS: dict[AirtimeCapability, str] = {
    AirtimeCapability.SELF_RECHARGE: "self recharge",
    AirtimeCapability.OTHER_RECHARGE: "recharge for others",
    AirtimeCapability.SCHEDULED: "scheduled recharge",
    AirtimeCapability.RECURRING: "recurring recharge",
}


@dataclass
class CapabilityDecision:
    """Structured decision from capability check."""

    allowed: bool
    missing: list[AirtimeCapability] = field(default_factory=list)
    prompt: str = ""
    suggested_action: str | None = None
    patch: dict[str, Any] = field(default_factory=dict)


def derive_requirements(
    user_message: str = "",
    requested_features: list[str] | None = None,
    is_self: bool = False,
) -> list[AirtimeCapability]:
    """Derive required capabilities from user message."""
    requires: set[AirtimeCapability] = set()

    if is_self:
        requires.add(AirtimeCapability.SELF_RECHARGE)
    else:
        requires.add(AirtimeCapability.OTHER_RECHARGE)

    requested = set(requested_features or [])
    msg = user_message.lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in msg for k in keywords)

    if "SCHEDULED" in requested or has_any(["schedule", "tomorrow", "later", "next"]):
        requires.add(AirtimeCapability.SCHEDULED)

    if "RECURRING" in requested or has_any(["every week", "weekly", "monthly", "recurring", "automatically"]):
        requires.add(AirtimeCapability.RECURRING)

    return list(requires)


def decide_capability(requires: list[AirtimeCapability]) -> CapabilityDecision:
    """Check capabilities and return structured decision."""
    missing = [cap for cap in requires if cap not in AIRTIME_SUPPORTS]

    if not missing:
        return CapabilityDecision(allowed=True)

    if AirtimeCapability.SCHEDULED in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[AirtimeCapability.SCHEDULED],
            suggested_action="proceed_immediate",
            patch={"scheduled": False, "schedule_date": None},
            prompt="Scheduled airtime purchases aren't available yet.\n\nWant me to proceed with an immediate recharge?",
        )

    if AirtimeCapability.RECURRING in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[AirtimeCapability.RECURRING],
            suggested_action="one_time",
            patch={"recurring": False, "recurring_schedule": None},
            prompt="Recurring airtime purchases aren't available yet.\n\nI can do a one-time recharge now. Proceed?",
        )

    label = CAPABILITY_LABELS.get(missing[0], missing[0].value)
    return CapabilityDecision(
        allowed=False,
        missing=[missing[0]],
        prompt=f"I get you — you want *{label}*.\n\nThat isn't available yet.",
    )
