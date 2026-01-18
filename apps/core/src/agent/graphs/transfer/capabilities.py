"""Transfer graph capability definitions.

Defines what the transfer graph supports and feature negotiation.
Capability check happens after extraction, before validation.

Architecture:
- Capabilities: What features we support (SINGLE_TRANSFER, SCHEDULED, etc.)
- Policies: Limits handled by validation layer (amount_validator.py)
- CapabilityDecision: Structured response with suggested_action + patch
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.transfer.models_extraction import TransferExtractionResult


class TransferCapability(str, Enum):
    """Features that can be required by a transfer."""

    SINGLE_TRANSFER = "single_transfer"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"
    INTERNATIONAL = "international"
    SPLIT_TRANSFER = "split_transfer"


TRANSFER_SUPPORTS: set[TransferCapability] = {
    TransferCapability.SINGLE_TRANSFER,
    # TransferCapability.SPLIT_TRANSFER,  # Enable when batch DAG is ready
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
        suggested_action: Action to take if user accepts (e.g., "proceed_immediate", "one_time")
        patch: State changes to apply if user accepts the suggestion
    """

    allowed: bool
    missing: list[TransferCapability] = field(default_factory=list)
    prompt: str = ""
    suggested_action: str | None = None
    patch: dict[str, Any] = field(default_factory=dict)


def derive_requirements(
    extraction: "TransferExtractionResult",
    user_message: str = "",
) -> list[TransferCapability]:
    """Derive required capabilities from extraction result."""
    requires: set[TransferCapability] = {TransferCapability.SINGLE_TRANSFER}

    requested = {str(f) for f in (extraction.requested_features or [])}

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


def decide_capability(
    requires: list[TransferCapability],
    extraction: "TransferExtractionResult | None" = None,
) -> CapabilityDecision:
    """
    Check capabilities and return structured decision.
    
    Priority order:
    1. International (hard block - no alternative)
    2. Scheduled (offer immediate)
    3. Recurring (offer one-time)
    4. Other missing capabilities
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

    # Priority 2: Scheduled (offer immediate)
    if TransferCapability.SCHEDULED in missing:
        return CapabilityDecision(
            allowed=False,
            missing=[TransferCapability.SCHEDULED],
            suggested_action="proceed_immediate",
            patch={"scheduled": False, "schedule_date": None},
            prompt="Scheduled transfers aren't available yet.\n\nWant me to proceed with an immediate transfer?",
        )

    # Priority 3: Recurring (offer one-time)
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
