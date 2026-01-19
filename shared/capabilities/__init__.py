"""Capability definitions for agent services.

Capabilities define what features each service supports and provide
negotiation logic for unsupported features.
"""

from shared.capabilities.transfer import (
    TransferCapability,
    TRANSFER_SUPPORTS,
    derive_requirements as derive_transfer_requirements,
    decide_capability as decide_transfer_capability,
    CapabilityDecision as TransferCapabilityDecision,
)
from shared.capabilities.airtime import (
    AirtimeCapability,
    AIRTIME_SUPPORTS,
    derive_requirements as derive_airtime_requirements,
    decide_capability as decide_airtime_capability,
    CapabilityDecision as AirtimeCapabilityDecision,
)
from shared.capabilities.data import (
    DataCapability,
    DATA_SUPPORTS,
    derive_requirements as derive_data_requirements,
    decide_capability as decide_data_capability,
    CapabilityDecision as DataCapabilityDecision,
)

__all__ = [
    # Transfer
    "TransferCapability",
    "TRANSFER_SUPPORTS",
    "derive_transfer_requirements",
    "decide_transfer_capability",
    "TransferCapabilityDecision",
    # Airtime
    "AirtimeCapability",
    "AIRTIME_SUPPORTS",
    "derive_airtime_requirements",
    "decide_airtime_capability",
    "AirtimeCapabilityDecision",
    # Data
    "DataCapability",
    "DATA_SUPPORTS",
    "derive_data_requirements",
    "decide_data_capability",
    "DataCapabilityDecision",
]
