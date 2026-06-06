"""Semantic decision dispatcher for context-frame follow-up answers."""

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_account_status import (
    format_account_status_explanation,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_decisions import (
    CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE,
    canonical_decision,
    decision_field_text,
    decision_rank_text,
    decision_target_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_responses import (
    format_details_response,
    format_entity_details,
    format_field_response,
    format_lookup_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_filtering import (
    find_filtered_entities,
    has_filters,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_ranking import ranked_entity
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_response_explain import (
    format_explain_result_response,
    format_frame_clarification_response,
    format_missing_amount_reference_response,
    format_unclear_grounded_target_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_response_selection import (
    format_compare_response,
    format_completeness_response,
    format_filter_response,
    format_selection_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import (
    account_status_grounded_entities,
    find_matching_entities,
)
from shared.types.planner import ContextFrameFollowupDecision


def format_semantic_decision_response(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    *,
    text: str = "",
    locale: str = "en",
) -> str | None:
    if decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        return None

    semantic_decision = canonical_decision(decision.decision)

    if semantic_decision == "start_new_task":
        status_matches = account_status_grounded_entities(frame, text)
        if len(status_matches) == 1:
            explanation = format_account_status_explanation(status_matches[0])
            if explanation:
                return explanation
        return None

    if semantic_decision == "replay_tasks":
        return None

    if semantic_decision == "answer_completeness":
        return format_completeness_response(frame, locale=locale)

    if semantic_decision == "show_details":
        target_text = decision_target_text(decision)
        field_text = decision_field_text(decision)
        rank_text = decision_rank_text(decision)
        if decision.selection_index is not None:
            idx = decision.selection_index - 1
            if 0 <= idx < len(frame.items):
                field_response = format_field_response(frame, [frame.items[idx]], field_text, locale=locale)
                return field_response or format_entity_details(frame, [frame.items[idx]], locale=locale)
        if rank_text:
            ranked = ranked_entity(frame, rank_text)
            if ranked is not None:
                return format_entity_details(frame, [ranked], locale=locale)
        if target_text:
            matches = find_matching_entities(frame, target_text)
            if matches:
                field_response = format_field_response(frame, matches, field_text, locale=locale)
                return field_response or format_entity_details(frame, matches, locale=locale)
            missing_amount_response = format_missing_amount_reference_response(frame, target_text, locale=locale)
            if missing_amount_response:
                return missing_amount_response
        matches = find_filtered_entities(frame, decision.filters)
        if matches:
            field_response = format_field_response(frame, matches, field_text, locale=locale)
            return field_response or format_entity_details(frame, matches, locale=locale)
        field_response = format_field_response(frame, frame.items, field_text, locale=locale)
        if field_response:
            return field_response
        return format_details_response(frame, locale=locale)

    if semantic_decision == "lookup_entity":
        target_text = decision_target_text(decision)
        if not target_text:
            return format_frame_clarification_response(frame, locale=locale)
        return format_lookup_response(frame, target_text, explicit_lookup=True, locale=locale)

    if semantic_decision == "filter_items":
        target_text = decision_target_text(decision)
        if not target_text and not decision.rank and not has_filters(decision.filters):
            return format_frame_clarification_response(frame, locale=locale)
        return format_filter_response(
            frame,
            target_text,
            rank_text=decision_rank_text(decision),
            filters=decision.filters,
            locale=locale,
        )

    if semantic_decision == "compare_items":
        return format_compare_response(
            frame,
            decision_target_text(decision) or None,
            rank_text=decision_rank_text(decision),
            filters=decision.filters,
            locale=locale,
        )

    if semantic_decision == "select_item":
        return format_selection_response(frame, decision, locale=locale)

    if semantic_decision == "explain_result":
        return format_explain_result_response(frame, decision, text=text, locale=locale)

    if semantic_decision == "unclear" and decision.confidence >= CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        grounded_target = format_unclear_grounded_target_response(frame, text, locale=locale)
        if grounded_target:
            return grounded_target
        return format_frame_clarification_response(frame, locale=locale)

    return None


__all__ = ["format_semantic_decision_response"]
