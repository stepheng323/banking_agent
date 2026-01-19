"""Transfer capability definitions.

Defines what transfer features are supported and provides capability negotiation.
Moved to shared layer to break circular imports.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TransferCapability(str, Enum):
    """Features that can be required by a transfer."""

    SINGLE_TRANSFER = "single_transfer"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"
    INTERNATIONAL = "international"
    SPLIT_TRANSFER = "split_transfer"


TRANSFER_SUPPORTS: set[TransferCapability] = {
    TransferCapability.SINGLE_TRANSFER,
}


CAPABILITY_LABELS: dict[TransferCapability, str] = {
    TransferCapability.SINGLE_TRANSFER: "single transfer",
    TransferCapability.SCHEDULED: "scheduled transfers",
    TransferCapability.RECURRING: "recurring transfers",
    TransferCapability.INTERNATIONAL: "international transfers",
    TransferCapability.SPLIT_TRANSFER: "split transfers",
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
    missing: list[TransferCapability] = field(default_factory=list)
    prompt: str = ""
    suggested_action: str | None = None
    patch: dict[str, Any] = field(default_factory=dict)


def derive_requirements(
    user_message: str = "",
    requested_features: list[str] | None = None,
) -> list[TransferCapability]:
    """Derive required capabilities from user message.
    
    Args:
        user_message: The user's message text
        requested_features: Optional list of explicitly requested features
    
    Returns:
        List of required capabilities
    """
    requires: set[TransferCapability] = {TransferCapability.SINGLE_TRANSFER}

    requested = set(requested_features or [])
    msg = user_message.lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in msg for k in keywords)

    if "SCHEDULED" in requested or has_any(["schedule", "tomorrow", "next week", "on friday", "on monday"]):
        requires.add(TransferCapability.SCHEDULED)

    if "RECURRING" in requested or has_any(["every week", "weekly", "monthly", "recurring", "automatically"]):
        requires.add(TransferCapability.RECURRING)

    if "INTERNATIONAL" in requested or has_any(["international", "overseas", "abroad"]):
        requires.add(TransferCapability.INTERNATIONAL)

    return list(requires)


def decide_capability(requires: list[TransferCapability]) -> CapabilityDecision:
    """Check capabilities and return structured decision.
    
    Priority order:
    1. International (hard block - no alternative)
    2. Scheduled (offer immediate)
    3. Recurring (offer one-time)
    """
    missing = [cap for cap in requires if cap not in TRANSFER_SUPPORTS]

    if not missing:
        return CapabilityDecision(allowed=True)

    if TransferCapability.INTERNATIONAL in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[TransferCapability.INTERNATIONAL],
            prompt="International transfers aren't available yet.\n\nI can only transfer to Nigerian banks for now.",
        )

    if TransferCapability.SCHEDULED in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[TransferCapability.SCHEDULED],
            suggested_action="proceed_immediate",
            patch={"scheduled": False, "schedule_date": None},
            prompt="Scheduled transfers aren't available yet.\n\nWant me to proceed with an immediate transfer?",
        )

    if TransferCapability.RECURRING in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[TransferCapability.RECURRING],
            suggested_action="one_time",
            patch={"recurring": False, "recurring_schedule": None},
            prompt="Recurring transfers aren't available yet.\n\nI can do a one-time transfer now. Proceed?",
        )

    label = CAPABILITY_LABELS.get(missing[0], missing[0].value)
    return CapabilityDecision(
        allowed=False,
        missing=[missing[0]],
        prompt=f"I get you — you want *{label}*.\n\nThat isn't available yet.",
    )
