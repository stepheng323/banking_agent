"""Typed patch accumulator for lifecycle finalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.context.referents.models import ShortTermReferentMemory


@dataclass(slots=True)
class FinalizeAccumulator:
    """Accumulates final LangGraph state updates for the finalize node."""

    outbox: list[dict[str, Any]]
    suppress_empty_fallback: bool = False
    _context_updates: dict[str, Any] = field(default_factory=dict)

    def set_suppress_empty_fallback(self, value: bool) -> None:
        self.suppress_empty_fallback = value

    def append_outbox(self, entry: dict[str, Any]) -> None:
        self.outbox.append(entry)

    def set_context_frames(self, frames: list[ContextFrame]) -> None:
        self._context_updates["context_frames"] = frames

    def set_referent_memory(self, referent_memory: ShortTermReferentMemory) -> None:
        self._context_updates["referent_memory"] = referent_memory

    def set_stashed_sessions(self, sessions: list[dict[str, Any]]) -> None:
        self._context_updates["stashed_sessions"] = sessions

    def to_updates(self) -> dict[str, Any]:
        return {
            "outbox": self.outbox,
            "tasks": {},
            "waves": [],
            "current_wave_index": 0,
            "pending_interrupt": None,
            "last_interrupt": None,
            "pin_verified": False,
            "last_callback": None,
            "session_stack": [],
            "active_domain": None,
            "suppress_empty_fallback": self.suppress_empty_fallback,
            **self._context_updates,
        }


__all__ = ["FinalizeAccumulator"]
