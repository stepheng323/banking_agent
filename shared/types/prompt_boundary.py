"""Typed classification primitives for prompt-boundary turns."""

from __future__ import annotations

from typing import Final, Literal, TypeAlias

PromptBoundaryIntent: TypeAlias = Literal[
    "instruction_override",
    "prompt_disclosure",
    "role_bypass",
    "orchestrator_bypass",
    "security_bypass",
    "ambiguous",
]

EXPLICIT_PROMPT_BOUNDARY_INTENTS: Final[frozenset[str]] = frozenset(
    {
        "instruction_override",
        "prompt_disclosure",
        "role_bypass",
        "orchestrator_bypass",
    }
)


def is_melkor_eligible(
    intent: str | None,
    confidence: float | None,
    *,
    threshold: float = 0.90,
) -> bool:
    """Return whether a boundary classification is explicit enough for Melkor copy."""
    return intent in EXPLICIT_PROMPT_BOUNDARY_INTENTS and (confidence or 0.0) >= threshold


__all__ = [
    "EXPLICIT_PROMPT_BOUNDARY_INTENTS",
    "PromptBoundaryIntent",
    "is_melkor_eligible",
]
