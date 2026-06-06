"""Full detail and lookup response formatting for context-frame answers."""

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_data_plans import (
    is_data_plan_entity,
    is_data_plan_frame,
    unique_data_plan_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_blocks import (
    detail_header,
    format_detail_block,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_fields import (
    CONTEXT_READ_LIST_LIMIT,
    display_key,
    entity_field_value,
    frame_noun,
    requested_field_keys,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import (
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_text import (
    lookup_tokens,
)
from banking.presentation.i18n.renderer import render_message


def format_field_response(
    frame: ContextFrame,
    entities: list[ContextEntity],
    requested_field: str | None,
    *,
    locale: str = "en",
) -> str | None:
    field = requested_field_keys(requested_field)
    if field is None or not entities:
        return None

    label, keys = field
    lines: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        value = entity_field_value(entity, keys)
        if value is None:
            continue
        prefix = f"{idx}. {entity.label}: " if len(entities) > 1 else ""
        lines.append(f"{prefix}{display_key(label)}: {value}")

    if not lines:
        return None
    if len(entities) > 1:
        header = detail_header(frame, locale=locale)
    else:
        header = entity.label or detail_header(frame, locale=locale)
    return f"{header}\n\n" + "\n".join(lines)


def format_details_response(frame: ContextFrame, *, locale: str = "en") -> str | None:
    items = unique_data_plan_entities(frame.items) if is_data_plan_frame(frame) else frame.items
    if len(items) > 1:
        blocks: list[str] = []
        for idx, entity in enumerate(items[:CONTEXT_READ_LIST_LIMIT], 1):
            block = format_detail_block(entity, ordinal=idx, locale=locale)
            if block:
                blocks.append(block)
        if not blocks:
            return None
        overflow = len(items) - len(blocks)
        suffix = (
            "\n\n"
            + render_message(
                "context_frame.followup.showing_items",
                locale,
                {
                    "shown_count": len(blocks),
                    "total_count": len(items),
                    "noun": frame_noun(frame.frame_type, plural=True, locale=locale),
                },
            )
            if overflow > 0
            else ""
        )
        return f"{detail_header(frame, locale=locale)}\n\n" + "\n\n".join(blocks) + suffix

    block = format_detail_block(items[0], locale=locale)
    if block is None:
        return None
    return f"{detail_header(frame, locale=locale)}\n\n" + block


def format_entity_details(frame: ContextFrame, entities: list[ContextEntity], *, locale: str = "en") -> str | None:
    if not entities:
        return None
    if all(is_data_plan_entity(entity) for entity in entities):
        entities = unique_data_plan_entities(entities)
    if len(entities) == 1:
        block = format_detail_block(entities[0], locale=locale)
        if block is None:
            return None
        return f"{detail_header(frame, locale=locale)}\n\n{block}"

    blocks: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        block = format_detail_block(entity, ordinal=idx, locale=locale)
        if block:
            blocks.append(block)
    if not blocks:
        return None
    overflow = len(entities) - len(blocks)
    suffix = (
        "\n\n"
        + render_message(
            "context_frame.followup.showing_matches",
            locale,
            {"shown_count": len(blocks), "total_count": len(entities)},
        )
        if overflow > 0
        else ""
    )
    return f"{detail_header(frame, locale=locale)}\n\n" + "\n\n".join(blocks) + suffix


def format_lookup_response(
    frame: ContextFrame,
    lookup_query: str,
    *,
    explicit_lookup: bool,
    locale: str = "en",
) -> str | None:
    query_tokens = lookup_tokens(lookup_query)
    if not query_tokens:
        return None

    matches = find_matching_entities(frame, lookup_query)
    query_label = " ".join(query_tokens).title()

    if not matches:
        if not explicit_lookup:
            return None
        return render_message(
            "context_frame.followup.missing_entity",
            locale,
            {"target": query_label, "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
        )

    if len(matches) == 1:
        block = format_detail_block(matches[0], locale=locale)
        if block is None:
            return render_message(
                "context_frame.followup.found_entity",
                locale,
                {"target": matches[0].label, "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
            )
        return f"{detail_header(frame, locale=locale)}\n\n{block}"

    blocks: list[str] = []
    for idx, entity in enumerate(matches[:CONTEXT_READ_LIST_LIMIT], 1):
        block = format_detail_block(entity, ordinal=idx, locale=locale)
        if block:
            blocks.append(block)
    if not blocks:
        labels = "\n".join(f"{idx}. {entity.label}" for idx, entity in enumerate(matches[:CONTEXT_READ_LIST_LIMIT], 1))
        return (
            render_message(
                "context_frame.followup.found_matching",
                locale,
                {"count": len(matches), "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
            )
            + f"\n\n{labels}"
        )

    overflow = len(matches) - len(blocks)
    suffix = (
        "\n\n"
        + render_message(
            "context_frame.followup.showing_matches",
            locale,
            {"shown_count": len(blocks), "total_count": len(matches)},
        )
        if overflow > 0
        else ""
    )
    header = render_message(
        "context_frame.followup.found_matches_in_frame",
        locale,
        {"count": len(matches), "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
    )
    return header + "\n\n" + "\n\n".join(blocks) + suffix


__all__ = [
    "format_details_response",
    "format_entity_details",
    "format_field_response",
    "format_lookup_response",
]
