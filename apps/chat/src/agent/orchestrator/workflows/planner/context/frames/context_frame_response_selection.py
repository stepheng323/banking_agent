"""Selection, filter, and comparison responses for context frames."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_decisions import (
    decision_field_text,
    decision_rank_text,
    decision_target_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_fields import (
    CONTEXT_READ_LIST_LIMIT,
    candidate_detail_fields,
    frame_noun,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_responses import (
    format_entity_details,
    format_field_response,
    format_lookup_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_filtering import (
    find_filtered_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_ranking import ranked_entity
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import (
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_text import lookup_tokens
from banking.presentation.i18n.renderer import render_message
from shared.types.planner import ContextFrameFollowupDecision, ContextFrameFollowupFilters


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _count_preview_metadata(frame: ContextFrame) -> tuple[int, int] | None:
    metadata = frame.metadata if isinstance(frame.metadata, dict) else {}
    if metadata.get("display_shape") != "count_preview":
        return None
    shown_count = _positive_int(metadata.get("shown_count"))
    total_count = _positive_int(metadata.get("total_count"))
    if shown_count is None or total_count is None or total_count <= shown_count:
        return None
    return shown_count, total_count


def format_completeness_response(frame: ContextFrame, *, locale: str = "en") -> str | None:
    count = len(frame.items)
    if count <= 0:
        return None
    preview_counts = _count_preview_metadata(frame)
    if preview_counts is not None:
        shown_count, total_count = preview_counts
        return render_message(
            "context_frame.followup.count_preview_clarification",
            locale,
            {
                "shown_count": shown_count,
                "total_count": total_count,
                "noun": frame_noun(frame.frame_type, plural=total_count != 1, locale=locale),
            },
        )
    if frame.frame_type == ContextFrameType.SCHEDULE_LIST:
        return render_message(
            "context_frame.followup.pending_scheduled_count",
            locale,
            {
                "count": count,
                "noun": frame_noun(frame.frame_type, plural=count != 1, locale=locale),
            },
        )
    if count == 1:
        return render_message(
            "context_frame.followup.only_found",
            locale,
            {"noun": frame_noun(frame.frame_type, plural=False, locale=locale)},
        )
    return render_message(
        "context_frame.followup.count_found",
        locale,
        {"count": count, "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
    )


def format_selection_response(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    *,
    locale: str = "en",
) -> str | None:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            field_response = format_field_response(
                frame,
                [frame.items[idx]],
                decision_field_text(decision),
                locale=locale,
            )
            return field_response or format_entity_details(frame, [frame.items[idx]], locale=locale)

    target_text = decision_target_text(decision)
    if target_text:
        ranked = ranked_entity(frame, decision_rank_text(decision))
        if ranked is not None:
            return format_entity_details(frame, [ranked], locale=locale)
        return format_lookup_response(frame, target_text, explicit_lookup=True, locale=locale)
    return None


def format_filter_response(
    frame: ContextFrame,
    target_text: str,
    *,
    rank_text: str | None = None,
    filters: ContextFrameFollowupFilters | None = None,
    locale: str = "en",
) -> str | None:
    ranked = ranked_entity(frame, rank_text)
    if ranked is not None:
        return format_entity_details(frame, [ranked], locale=locale)

    matches = find_filtered_entities(frame, filters)
    if matches:
        return format_entity_details(frame, matches, locale=locale)

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
        return render_message(
            "context_frame.followup.missing_entity",
            locale,
            {"target": query_label, "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
        )
    return format_entity_details(frame, matches, locale=locale)


def format_compare_response(
    frame: ContextFrame,
    target_text: str | None,
    *,
    rank_text: str | None = None,
    filters: ContextFrameFollowupFilters | None = None,
    locale: str = "en",
) -> str | None:
    ranked = ranked_entity(frame, rank_text)
    if ranked is not None:
        return format_entity_details(frame, [ranked], locale=locale)

    entities = find_filtered_entities(frame, filters)
    if not entities:
        entities = find_matching_entities(frame, target_text) if target_text else frame.items
    if len(entities) < 2:
        entities = frame.items
    if len(entities) < 2:
        return format_entity_details(frame, entities, locale=locale)

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
    suffix = (
        "\n\n"
        + render_message(
            "context_frame.followup.showing_items",
            locale,
            {
                "shown_count": len(blocks),
                "total_count": len(entities),
                "noun": frame_noun(frame.frame_type, plural=True, locale=locale),
            },
        )
        if overflow > 0
        else ""
    )
    return render_message("context_frame.followup.comparison_header", locale) + "\n\n" + "\n\n".join(blocks) + suffix


__all__ = [
    "format_compare_response",
    "format_completeness_response",
    "format_filter_response",
    "format_selection_response",
]
