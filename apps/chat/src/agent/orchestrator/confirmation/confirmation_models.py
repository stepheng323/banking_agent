"""Typed contracts for prompt-scoped confirmation decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

ConfirmationAction = Literal["approve", "reject", "modify", "new_request", "unclear"]
ConfirmationDecisionSource = Literal["fastpath", "llm", "guardrail"]
ConfirmationPromptKind = Literal[
    "transaction_confirmation",
    "resume_prompt",
    "beneficiary_save",
    "amount_suggestion",
]

APPROVAL_CONFIDENCE_THRESHOLD = 0.90
REJECTION_CONFIDENCE_THRESHOLD = 0.85


class ConfirmationDecisionOutput(BaseModel):
    """Structured LLM output for prompt-scoped confirmation decisions."""

    action: ConfirmationAction = Field(
        description="Classify only the reply to the active prompt: approve, reject, modify, new_request, or unclear."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="llm_classification")
    custom_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConfirmationDecision:
    action: ConfirmationAction
    source: ConfirmationDecisionSource
    confidence: float
    reason: str
    custom_data: dict[str, Any] | None = field(default=None)

    @property
    def is_approval(self) -> bool:
        return self.action == "approve"

    @property
    def is_rejection(self) -> bool:
        return self.action == "reject"

    @property
    def is_unclear(self) -> bool:
        return self.action == "unclear"


__all__ = [
    "APPROVAL_CONFIDENCE_THRESHOLD",
    "REJECTION_CONFIDENCE_THRESHOLD",
    "ConfirmationAction",
    "ConfirmationDecision",
    "ConfirmationDecisionOutput",
    "ConfirmationDecisionSource",
    "ConfirmationPromptKind",
]
