from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_decisions import (
    CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE,
    canonical_decision,
    frame_domain,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_focus import (
    context_frames_after_surface_answer_for_view,
    refresh_context_frame_for_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_selection import (
    decision_with_grounding_hints,
    select_frame_for_decision_from_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_types import (
    ContextFrameFollowupResponse,
    SurfaceAnswerRequest,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_schedule import (
    build_schedule_management_result_for_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_semantic_response import (
    format_semantic_decision_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
    context_frame_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay import (
    build_context_frame_replay_response_for_view,
)
from shared.types.planner import (
    ContextFrameFollowupDecision,
    ContextFrameReplayModifier,
)


def _build_replay_followup_response(
    request: SurfaceAnswerRequest,
    decision: ContextFrameFollowupDecision,
) -> ContextFrameFollowupResponse | None:
    if decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        return None
    frame = select_frame_for_decision_from_view(request.state_view, decision)
    if frame is None or not frame.items:
        return None
    replay_result = build_context_frame_replay_response_for_view(
        request.state_view,
        frame,
        decision,
        request.text,
        replay_modifier=request.replay_modifier,
    )
    if replay_result is None:
        return None
    return ContextFrameFollowupResponse(
        response=replay_result.response,
        semantic_path_shape=replay_result.semantic_path_shape,
        recent_domain_focus=replay_result.recent_domain_focus,
        context_frames=refresh_context_frame_for_view(request.state_view, frame),
        tasks=replay_result.tasks,
        waves=replay_result.waves,
    )


def _build_schedule_followup_response(
    request: SurfaceAnswerRequest,
    decision: ContextFrameFollowupDecision,
) -> ContextFrameFollowupResponse | None:
    frame = select_frame_for_decision_from_view(request.state_view, decision)
    if frame is None or not frame.items:
        return None
    schedule_result = build_schedule_management_result_for_view(request.state_view, frame, decision, request.text)
    if schedule_result is None:
        return None
    return ContextFrameFollowupResponse(
        recent_domain_focus=schedule_result.recent_domain_focus,
        context_frames=context_frames_after_surface_answer_for_view(request.state_view, frame, decision),
        tasks=schedule_result.tasks,
        waves=schedule_result.waves,
    )


def _build_semantic_followup_response(
    request: SurfaceAnswerRequest,
    decision: ContextFrameFollowupDecision,
) -> ContextFrameFollowupResponse | None:
    frame = select_frame_for_decision_from_view(request.state_view, decision)
    if frame is None or not frame.items:
        return None
    response = format_semantic_decision_response(frame, decision, text=request.text, locale=request.locale)
    if not response:
        return None
    return ContextFrameFollowupResponse(
        response=response,
        recent_domain_focus=frame_domain(frame.frame_type),
        context_frames=context_frames_after_surface_answer_for_view(request.state_view, frame, decision),
    )


def build_context_frame_followup_response_from_request(
    request: SurfaceAnswerRequest,
) -> ContextFrameFollowupResponse | None:
    """Build a grounded follow-up response from a typed surface-answer request."""
    decision = decision_with_grounding_hints(request.decision, request.text) if request.decision is not None else None
    if decision is None:
        return None

    decision_key = canonical_decision(decision.decision)
    if decision_key == "replay_tasks":
        return _build_replay_followup_response(request, decision)
    if decision_key in {"edit_schedule", "cancel_schedule"}:
        return _build_schedule_followup_response(request, decision)
    return _build_semantic_followup_response(request, decision)


def build_context_frame_followup_response(
    state: OrchestratorState,
    text: str,
    *,
    decision: ContextFrameFollowupDecision | None = None,
    replay_modifier: ContextFrameReplayModifier | None = None,
    locale: str = "en",
) -> ContextFrameFollowupResponse | None:
    return build_context_frame_followup_response_for_view(
        context_frame_state_view(state),
        text,
        decision=decision,
        replay_modifier=replay_modifier,
        locale=locale,
    )


def build_context_frame_followup_response_for_view(
    state_view: ContextFrameStateView,
    text: str,
    *,
    decision: ContextFrameFollowupDecision | None = None,
    replay_modifier: ContextFrameReplayModifier | None = None,
    locale: str = "en",
) -> ContextFrameFollowupResponse | None:
    """Build a grounded follow-up response from current state and decision."""
    return build_context_frame_followup_response_from_request(
        SurfaceAnswerRequest(
            state_view=state_view,
            text=text,
            decision=decision,
            replay_modifier=replay_modifier,
            locale=locale,
        )
    )


__all__ = [
    "build_context_frame_followup_response",
    "build_context_frame_followup_response_for_view",
    "build_context_frame_followup_response_from_request",
]
