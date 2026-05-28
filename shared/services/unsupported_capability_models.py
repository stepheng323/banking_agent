"""Shared models for unsupported capability routing."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

SUPPORTED_BANKING_ALTERNATIVES = "transfers, airtime/data, balances, and transaction queries"
UNSUPPORTED_CAPABILITY_SEMANTIC_CONFIDENCE = 0.84
UNSUPPORTED_BOUNDARY_TURN_CONFIDENCE = 0.78
SUPPORTED_BANKING_ALTERNATIVES_BY_LOCALE: Mapping[str, str] = {
    "en": SUPPORTED_BANKING_ALTERNATIVES,
    "pcm": "transfers, airtime/data, balances, and transaction queries",
    "yo": "transfer, airtime/data, balance, ati wiwa transaction",
    "ha": "transfer, airtime/data, balance, da binciken transaction",
    "ig": "transfer, airtime/data, balance, na nyocha transaction",
}


@dataclass(frozen=True, slots=True)
class UnsupportedCapability:
    key: str
    label: str
    policy_label: str
    patterns: tuple[re.Pattern[str], ...]
    followup_terms: tuple[str, ...]
    planner_alternatives: tuple[str, ...]
    supported_alternatives: str = SUPPORTED_BANKING_ALTERNATIVES
    safety_note: str | None = None
    labels_by_locale: Mapping[str, str] = field(default_factory=dict)
    supported_alternatives_by_locale: Mapping[str, str] = field(default_factory=dict)


UnsupportedCapabilitySemanticAction = Literal["unsupported", "mixed", "supported_or_other", "unclear"]
UnsupportedBoundaryTurnAction = Literal[
    "same_unsupported",
    "new_unsupported",
    "supported_banking",
    "unrelated",
    "unclear",
]


class UnsupportedCapabilitySemanticOutput(BaseModel):
    """Structured LLM output for unsupported capability classification."""

    action: UnsupportedCapabilitySemanticAction = Field(
        description=(
            "unsupported when the user asks only for an unsupported capability; mixed when the turn has both a "
            "supported banking request and an unsupported capability; supported_or_other for supported banking, "
            "casual, or unrelated turns; unclear when uncertain."
        )
    )
    capability_key: str | None = Field(
        default=None,
        description="One known unsupported capability key, or null if none is clearly present.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="semantic_classification")


class UnsupportedBoundaryTurnOutput(BaseModel):
    """Structured LLM output for turns after an unsupported capability refusal."""

    action: UnsupportedBoundaryTurnAction = Field(
        description=(
            "same_unsupported when the user continues the active unsupported topic; new_unsupported when they ask for "
            "a different known unsupported capability; supported_banking for a fresh supported banking request; "
            "unrelated for casual or unrelated turns; unclear when uncertain."
        )
    )
    capability_key: str | None = Field(
        default=None,
        description="Known unsupported capability key for same_unsupported/new_unsupported, otherwise null.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="boundary_turn_classification")
