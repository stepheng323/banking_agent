"""Frame selection helpers for context-frame follow-up answers."""

import time

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_decisions import (
    canonical_decision,
    decision_rank_text,
    decision_target_text,
    frame_domain,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_filtering import (
    find_filtered_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_ranking import ranked_entity
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_search import (
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_text import amount_reference_values
from shared.types.planner import ContextFrameFollowupDecision


def active_context_frames(state: OrchestratorState) -> list[ContextFrame]:
    now = int(time.time())
    return [frame for frame in state.context_frames if frame.items and (frame.created_at_ts + frame.ttl_seconds) > now]


def _decision_has_entity_match(frame: ContextFrame, decision: ContextFrameFollowupDecision) -> bool:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        return 0 <= idx < len(frame.items)

    if ranked_entity(frame, decision_rank_text(decision)) is not None:
        return True

    if find_filtered_entities(frame, decision.filters):
        return True

    target_text = decision_target_text(decision)
    return bool(target_text and find_matching_entities(frame, target_text))


def _frame_supports_decision(frame: ContextFrame, decision: ContextFrameFollowupDecision) -> bool:
    semantic_decision = canonical_decision(decision.decision)
    if semantic_decision in {"start_new_task", "unclear", "answer_completeness", "compare_items", "replay_tasks"}:
        return True
    if semantic_decision in {"edit_schedule", "cancel_schedule"}:
        return frame.frame_type == ContextFrameType.SCHEDULE_LIST
    if semantic_decision in {"show_details", "select_item", "filter_items", "lookup_entity", "explain_result"}:
        if _decision_has_entity_match(frame, decision):
            return True
        if (
            semantic_decision == "show_details"
            and decision.requested_field
            and len(frame.items) == 1
            and frame.frame_type in {ContextFrameType.TRANSACTION_DETAIL, ContextFrameType.RECEIPT}
        ):
            return True
    return False


def select_frame_for_decision(
    state: OrchestratorState,
    decision: ContextFrameFollowupDecision,
) -> ContextFrame | None:
    active_frames = active_context_frames(state)
    if not active_frames:
        return None

    latest = active_frames[-1]
    if _frame_supports_decision(latest, decision):
        return latest

    latest_domain = frame_domain(latest.frame_type)
    for frame in reversed(active_frames[:-1]):
        if latest_domain and frame_domain(frame.frame_type) != latest_domain:
            continue
        if _frame_supports_decision(frame, decision):
            return frame
    return latest


def decision_with_grounding_hints(
    decision: ContextFrameFollowupDecision,
    text: str,
) -> ContextFrameFollowupDecision:
    """Promote obvious visible references from raw text when the classifier omitted them."""
    semantic_decision = canonical_decision(decision.decision)
    if semantic_decision not in {"show_details", "select_item", "filter_items", "lookup_entity", "explain_result"}:
        return decision
    if decision_target_text(decision):
        return decision
    if not amount_reference_values(text):
        return decision
    return decision.model_copy(update={"target_text": text})


__all__ = [
    "active_context_frames",
    "decision_with_grounding_hints",
    "select_frame_for_decision",
]
