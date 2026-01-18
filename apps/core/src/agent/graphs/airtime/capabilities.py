"""Airtime graph capability definitions.

Defines what the airtime graph supports and feature negotiation.
Capability check happens after extraction, before validation.

Architecture:
- Capabilities: What features we support (SELF_RECHARGE, SCHEDULED, etc.)
- Policies: Limits handled by validation layer (amount_validator.py)
- CapabilityDecision: Structured response with suggested_action + patch
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.airtime.models import AirtimeExtractionResult


class AirtimeCapability(str, Enum):
    """Features that can be required for airtime purchase."""

    SELF_RECHARGE = "self_recharge"
    OTHER_RECHARGE = "other_recharge"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"


# What we currently support
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
    """Structured decision from capability check.
    
    Attributes:
        allowed: Whether the request can proceed
        missing: Capabilities required but not supported
        prompt: User-facing message explaining the issue
        suggested_action: Action to take if user accepts
        patch: State changes to apply if user accepts
    """

    allowed: bool
    missing: list[AirtimeCapability] = field(default_factory=list)
    prompt: str = ""
    suggested_action: str | None = None
    patch: dict[str, Any] = field(default_factory=dict)


def derive_requirements(
    extraction: "AirtimeExtractionResult",
    user_message: str = "",
) -> list[AirtimeCapability]:
    """Derive required capabilities from extraction result."""
    requires: set[AirtimeCapability] = set()

    if extraction.entities:
        if extraction.entities.is_self:
            requires.add(AirtimeCapability.SELF_RECHARGE)
        elif extraction.entities.recipient_phone:
            requires.add(AirtimeCapability.OTHER_RECHARGE)

    # Prefer model-extracted features; fall back to keywords
    requested = {str(f) for f in (extraction.requested_features or [])}

    msg = user_message.lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in msg for k in keywords)

    if "SCHEDULED" in requested or has_any(["schedule", "tomorrow", "later", "next"]):
        requires.add(AirtimeCapability.SCHEDULED)

    if "RECURRING" in requested or has_any(["every week", "weekly", "monthly", "recurring", "automatically"]):
        requires.add(AirtimeCapability.RECURRING)

    return list(requires)


def decide_capability(
    requires: list[AirtimeCapability],
    extraction: "AirtimeExtractionResult | None" = None,
) -> CapabilityDecision:
    """
    Check capabilities and return structured decision.
    
    Priority order:
    1. Scheduled (offer immediate)
    2. Recurring (offer one-time)
    3. Other missing capabilities
    """
    missing = [cap for cap in requires if cap not in AIRTIME_SUPPORTS]

    if not missing:
        return CapabilityDecision(allowed=True)

    # Priority 1: Scheduled (offer immediate)
    if AirtimeCapability.SCHEDULED in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[AirtimeCapability.SCHEDULED],
            suggested_action="proceed_immediate",
            patch={"scheduled": False, "schedule_date": None},
            prompt="Scheduled airtime purchases aren't available yet.\n\nWant me to proceed with an immediate recharge?",
        )

    # Priority 2: Recurring (offer one-time)
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
