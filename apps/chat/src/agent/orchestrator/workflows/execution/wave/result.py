"""Typed execution wave result contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ExecutionWavePhase = Literal[
    "no_current_wave",
    "pending_interrupt",
    "recipient_review_block",
    "batch_funding_block",
    "finalized",
]


@dataclass(frozen=True, slots=True)
class ExecutionWaveResult:
    """Log/test-only wrapper around execution wave updates."""

    updates: dict[str, Any]
    phase: ExecutionWavePhase
    current_wave: list[str]
    pending_interrupt_kind: str | None = None
    worker_phase_entered: bool = False


__all__ = ["ExecutionWavePhase", "ExecutionWaveResult"]
