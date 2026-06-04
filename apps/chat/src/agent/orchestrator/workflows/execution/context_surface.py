"""Typed context-frame access for execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.context.referents.models import ShortTermReferentMemory
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager

if TYPE_CHECKING:
    from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext


@dataclass(frozen=True)
class ExecutionContextSurface:
    """Read-only typed facade over execution context frames and referents."""

    state: OrchestratorState

    @property
    def frames(self) -> list[ContextFrame]:
        return self.state.context_frames

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def referent_memory(self) -> ShortTermReferentMemory:
        return self.state.referent_memory

    def referent_memory_payload(self) -> dict[str, Any]:
        return self.referent_memory.model_dump(mode="json")


def context_surface(state: OrchestratorState) -> ExecutionContextSurface:
    return ExecutionContextSurface(state)


def push_context_frame(ctx: ExecutionTurnContext, frame: ContextFrame) -> None:
    OrchestratorContextManager().push_frame(ctx.state, frame)
    surface = context_surface(ctx.state)
    ctx.accumulator.set_context_frames(surface.frames)
    ctx.accumulator.set_referent_memory(surface.referent_memory)


def replace_context_frames(ctx: ExecutionTurnContext, frames: list[ContextFrame]) -> None:
    ctx.accumulator.set_context_frames(frames)


def sync_referent_memory(ctx: ExecutionTurnContext) -> None:
    ctx.accumulator.set_referent_memory(context_surface(ctx.state).referent_memory)


__all__ = [
    "ExecutionContextSurface",
    "context_surface",
    "push_context_frame",
    "replace_context_frames",
    "sync_referent_memory",
]
