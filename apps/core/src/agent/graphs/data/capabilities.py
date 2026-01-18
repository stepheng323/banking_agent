"""Data graph capability definitions.

Defines what the data purchase graph supports and feature negotiation.
Capability check happens after extraction, before plan selection.

Architecture:
- Capabilities: What features we support (SELF_PURCHASE, SCHEDULED, etc.)
- Policies: Limits handled by validation layer
- CapabilityDecision: Structured response with suggested_action + patch
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.data.models_extraction import DataExtractionResult


class DataCapability(str, Enum):
    """Features that can be required for data purchase."""

    SELF_PURCHASE = "self_purchase"
    OTHER_PURCHASE = "other_purchase"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"


# What we currently support
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
    """Structured decision from capability check.
    
    Attributes:
        allowed: Whether the request can proceed
        missing: Capabilities required but not supported
        prompt: User-facing message explaining the issue
        suggested_action: Action to take if user accepts
        patch: State changes to apply if user accepts
    """

    allowed: bool
    missing: list[DataCapability] = field(default_factory=list)
    prompt: str = ""
    suggested_action: str | None = None
    patch: dict[str, Any] = field(default_factory=dict)


def derive_requirements(
    extraction: "DataExtractionResult",
    user_message: str = "",
) -> list[DataCapability]:
    """Derive required capabilities from extraction result."""
    requires: set[DataCapability] = set()

    if extraction.entities:
        if extraction.entities.is_self:
            requires.add(DataCapability.SELF_PURCHASE)
        elif extraction.entities.recipient_phone:
            requires.add(DataCapability.OTHER_PURCHASE)

    # Prefer model-extracted features; fall back to keywords
    requested = {str(f) for f in (extraction.requested_features or [])}

    msg = user_message.lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in msg for k in keywords)

    if "SCHEDULED" in requested or has_any(["schedule", "tomorrow", "later", "next"]):
        requires.add(DataCapability.SCHEDULED)

    if "RECURRING" in requested or has_any(["every week", "weekly", "monthly", "recurring", "automatically"]):
        requires.add(DataCapability.RECURRING)

    return list(requires)


def decide_capability(
    requires: list[DataCapability],
    extraction: "DataExtractionResult | None" = None,
) -> CapabilityDecision:
    """
    Check capabilities and return structured decision.
    
    Priority order:
    1. Scheduled (offer immediate)
    2. Recurring (offer one-time)
    3. Other missing capabilities
    """
    missing = [cap for cap in requires if cap not in DATA_SUPPORTS]

    if not missing:
        return CapabilityDecision(allowed=True)

    # Priority 1: Scheduled (offer immediate)
    if DataCapability.SCHEDULED in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[DataCapability.SCHEDULED],
            suggested_action="proceed_immediate",
            patch={"scheduled": False, "schedule_date": None},
            prompt="Scheduled data purchases aren't available yet.\n\nWant me to proceed with an immediate purchase?",
        )

    # Priority 2: Recurring (offer one-time)
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
