from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_context_builder import (
    build_context_frame_followup_context,
    build_context_frame_followup_context_for_state,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_response_builder import (
    build_context_frame_followup_response,
    build_context_frame_followup_response_from_request,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_types import (
    ContextFrameFollowupResponse,
    SurfaceAnswerRequest,
)
from shared.types.planner import (
    ContextFrameFollowupDecision,
    ContextFrameReplayModifier,
)


class SurfaceAnswerEngine:
    """Ground conversational follow-ups against the latest displayed frame."""

    def build_context(self, frame: ContextFrame) -> str:
        """Build a compact LLM context for interpreting frame follow-ups."""
        return build_context_frame_followup_context(frame)

    def build_state_context(self, state: OrchestratorState) -> str:
        """Build LLM context from active frames, preserving current focus and prior lists."""
        return build_context_frame_followup_context_for_state(state)

    def answer(self, request: SurfaceAnswerRequest) -> ContextFrameFollowupResponse | None:
        """Answer a grounded follow-up from current state and a typed decision."""
        return build_context_frame_followup_response_from_request(request)


_SURFACE_ANSWER_ENGINE = SurfaceAnswerEngine()


def build_surface_answer_context(frame: ContextFrame) -> str:
    return _SURFACE_ANSWER_ENGINE.build_context(frame)


def build_surface_answer_context_for_state(state: OrchestratorState) -> str:
    return _SURFACE_ANSWER_ENGINE.build_state_context(state)


def build_surface_answer_response(
    state: OrchestratorState,
    text: str,
    *,
    decision: ContextFrameFollowupDecision | None = None,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> ContextFrameFollowupResponse | None:
    return build_context_frame_followup_response(
        state,
        text,
        decision=decision,
        replay_modifier=replay_modifier,
    )


__all__ = [
    "SurfaceAnswerEngine",
    "build_surface_answer_context",
    "build_surface_answer_context_for_state",
    "build_surface_answer_response",
]
