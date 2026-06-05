"""Request and response types for context-frame follow-up answers."""

from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
)
from shared.types.planner import (
    ContextFrameFollowupDecision,
    ContextFrameReplayModifier,
)


@dataclass(frozen=True, slots=True)
class ContextFrameFollowupResponse:
    response: str | None = None
    semantic_path_shape: str = "context_frame_followup"
    recent_domain_focus: str | None = None
    context_frames: list[ContextFrame] | None = None
    tasks: dict[str, TaskSpec] | None = None
    waves: list[list[str]] | None = None


@dataclass(frozen=True, slots=True)
class SurfaceAnswerRequest:
    state_view: ContextFrameStateView
    text: str
    decision: ContextFrameFollowupDecision | None = None
    replay_modifier: ContextFrameReplayModifier | None = None
    locale: str = "en"


__all__ = ["ContextFrameFollowupResponse", "SurfaceAnswerRequest"]
