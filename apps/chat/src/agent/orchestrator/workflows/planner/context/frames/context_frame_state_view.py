"""Typed state surface for context-frame follow-up helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


@dataclass(frozen=True, slots=True)
class ContextFrameStateView:
    """Read-only facade for context-frame follow-up and replay state."""

    state: OrchestratorState

    @property
    def phone_number(self) -> str:
        return self.state.phone_number

    @property
    def loaded_context_or_empty(self) -> dict[str, Any]:
        loaded_context = self.state.loaded_context
        return loaded_context if isinstance(loaded_context, dict) else {}

    @property
    def context_frames(self) -> list[ContextFrame]:
        return list(self.state.context_frames)

    @property
    def active_context_frames(self) -> list[ContextFrame]:
        now = int(time.time())
        return [
            frame for frame in self.context_frames if frame.items and (frame.created_at_ts + frame.ttl_seconds) > now
        ]

    def latest_active_frame(self, frame_type: ContextFrameType | None = None) -> ContextFrame | None:
        for frame in reversed(self.active_context_frames):
            if frame_type is not None and frame.frame_type != frame_type:
                continue
            return frame
        return None

    @property
    def task_ids(self) -> set[str]:
        return set(self.state.tasks.keys())


def context_frame_state_view(state: OrchestratorState) -> ContextFrameStateView:
    return ContextFrameStateView(state)


__all__ = ["ContextFrameStateView", "context_frame_state_view"]
