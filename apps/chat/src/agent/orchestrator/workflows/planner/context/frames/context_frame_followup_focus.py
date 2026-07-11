"""Focus-frame refresh helpers for context-frame follow-up answers."""

import time

from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_decisions import (
    canonical_decision,
    decision_rank_text,
    decision_target_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_filtering import (
    find_filtered_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_ranking import ranked_entity
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import (
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
    context_frame_state_view,
)
from shared.types.planner import ContextFrameFollowupDecision


def _single_focus_entity_for_decision(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> ContextEntity | None:
    semantic_decision = canonical_decision(decision.decision)
    if semantic_decision not in {"show_details", "select_item", "filter_items", "lookup_entity", "explain_result"}:
        return None

    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            return frame.items[idx]
        return None

    ranked = ranked_entity(frame, decision_rank_text(decision))
    if ranked is not None:
        return ranked

    matches = find_filtered_entities(frame, decision.filters)
    if len(matches) == 1:
        return matches[0]

    target_text = decision_target_text(decision)
    if target_text:
        matches = find_matching_entities(frame, target_text)
        if len(matches) == 1:
            return matches[0]

    if len(frame.items) == 1:
        return frame.items[0]
    return None


def _detail_frame_type_for_focus(frame: ContextFrame) -> ContextFrameType | None:
    if frame.frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.RECEIPT}:
        return ContextFrameType.TRANSACTION_DETAIL
    return None


def _append_focus_detail_frame(
    frames: list[ContextFrame],
    source_frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> list[ContextFrame]:
    detail_type = _detail_frame_type_for_focus(source_frame)
    if detail_type is None:
        return frames

    entity = _single_focus_entity_for_decision(source_frame, decision)
    if entity is None:
        return frames

    now = int(time.time())
    metadata = dict(source_frame.metadata) if isinstance(source_frame.metadata, dict) else {}
    # The detail is still a lens over the query that produced the list. Preserve
    # the contract and pagination facts so the next referential turn remains a
    # query continuation instead of falling through to generic routing.
    metadata.pop("query_frame", None)
    metadata.update(
        {
            "surface_mode": "direct_answer",
            "surface_context": {
                "type": "single_transaction",
                "selected_item_id": entity.entity_id,
                "parent_visible_count": min(len(source_frame.items), 5),
            },
        }
    )
    detail_frame = ContextFrame(
        frame_id=f"{source_frame.frame_id}:focus:{entity.entity_id or now}",
        frame_type=detail_type,
        items=[entity.model_copy(deep=True)],
        focus_index=0,
        source_message_id=source_frame.source_message_id,
        created_at_ts=now,
        ttl_seconds=source_frame.ttl_seconds,
        metadata=metadata,
    )
    return [*frames, detail_frame][-ContextFrameManager().max_frames :]


def context_frames_after_surface_answer(
    state: OrchestratorState,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> list[ContextFrame]:
    return context_frames_after_surface_answer_for_view(context_frame_state_view(state), frame, decision)


def context_frames_after_surface_answer_for_view(
    state_view: ContextFrameStateView,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> list[ContextFrame]:
    refreshed = refresh_context_frame_for_view(state_view, frame)
    return _append_focus_detail_frame(refreshed, frame, decision)


def refresh_context_frame(state: OrchestratorState, active_frame: ContextFrame) -> list[ContextFrame]:
    return refresh_context_frame_for_view(context_frame_state_view(state), active_frame)


def refresh_context_frame_for_view(state_view: ContextFrameStateView, active_frame: ContextFrame) -> list[ContextFrame]:
    now = int(time.time())
    refreshed: list[ContextFrame] = []
    for frame in state_view.context_frames:
        if frame.frame_id == active_frame.frame_id:
            refreshed.append(frame.model_copy(update={"created_at_ts": now}))
        elif (frame.created_at_ts + frame.ttl_seconds) > now:
            refreshed.append(frame)
    return refreshed


__all__ = [
    "context_frames_after_surface_answer",
    "context_frames_after_surface_answer_for_view",
    "refresh_context_frame",
    "refresh_context_frame_for_view",
]
