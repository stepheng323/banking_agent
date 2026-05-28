"""Selection, filter, and comparison responses for context frames."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_decisions import (
    decision_field_text,
    decision_rank_text,
    decision_target_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_detail_fields import (
    candidate_detail_fields,
    frame_noun,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_detail_responses import (
    format_entity_details,
    format_field_response,
    format_lookup_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_filtering import (
    find_filtered_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_ranking import ranked_entity
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_search import (
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_text import lookup_tokens
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import (
    CONTEXT_READ_LIST_LIMIT,
)
from shared.types.planner import ContextFrameFollowupDecision


def format_completeness_response(frame: ContextFrame) -> str | None:
    count = len(frame.items)
    if count <= 0:
        return None
    if frame.frame_type == ContextFrameType.SCHEDULE_LIST:
        noun = "transaction" if count == 1 else "transactions"
        return f"You have {count} pending scheduled {noun}."
    if count == 1:
        return f"Yes. That's the only {frame_noun(frame.frame_type, plural=False)} I found."
    return f"Yes. Those are the {count} {frame_noun(frame.frame_type, plural=True)} I found."


def format_selection_response(frame: ContextFrame, decision: ContextFrameFollowupDecision) -> str | None:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            field_response = format_field_response(frame, [frame.items[idx]], decision_field_text(decision))
            return field_response or format_entity_details(frame, [frame.items[idx]])

    target_text = decision_target_text(decision)
    if target_text:
        ranked = ranked_entity(frame, decision_rank_text(decision))
        if ranked is not None:
            return format_entity_details(frame, [ranked])
        return format_lookup_response(frame, target_text, explicit_lookup=True)
    return None


def format_filter_response(
    frame: ContextFrame,
    target_text: str,
    *,
    rank_text: str | None = None,
    filters: Any = None,
) -> str | None:
    ranked = ranked_entity(frame, rank_text)
    if ranked is not None:
        return format_entity_details(frame, [ranked])

    matches = find_filtered_entities(frame, filters)
    if matches:
        return format_entity_details(frame, matches)

    matches = find_matching_entities(frame, target_text)
    if not matches:
        query_label = " ".join(lookup_tokens(target_text)).title()
        if not query_label and filters is not None:
            query_label = " ".join(
                str(value).strip()
                for value in (
                    filters.transaction_type,
                    filters.status,
                    filters.direction,
                    filters.bank,
                    filters.counterparty,
                )
                if value
            ).title()
        if not query_label:
            return None
        return f"I don't see {query_label} in the {frame_noun(frame.frame_type, plural=True)} I showed."
    return format_entity_details(frame, matches)


def format_compare_response(
    frame: ContextFrame,
    target_text: str | None,
    *,
    rank_text: str | None = None,
    filters: Any = None,
) -> str | None:
    ranked = ranked_entity(frame, rank_text)
    if ranked is not None:
        return format_entity_details(frame, [ranked])

    entities = find_filtered_entities(frame, filters)
    if not entities:
        entities = find_matching_entities(frame, target_text) if target_text else frame.items
    if len(entities) < 2:
        entities = frame.items
    if len(entities) < 2:
        return format_entity_details(frame, entities)

    blocks: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        fields = candidate_detail_fields(entity)
        if not fields:
            blocks.append(f"{idx}. {entity.label}")
            continue
        lines = [f"{idx}. {entity.label}"]
        for label, value in fields:
            lines.append(f"{label}: {value}")
        blocks.append("\n".join(lines))

    if not blocks:
        return None
    overflow = len(entities) - len(blocks)
    suffix = f"\n\nShowing {len(blocks)} of {len(entities)} items." if overflow > 0 else ""
    return "Comparison\n\n" + "\n\n".join(blocks) + suffix


__all__ = [
    "format_compare_response",
    "format_completeness_response",
    "format_filter_response",
    "format_selection_response",
]
