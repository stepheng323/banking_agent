"""Explanation and clarification responses for context frames."""

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_account_status import (
    format_account_status_explanation,
    pending_account_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_decisions import (
    decision_field_text,
    decision_target_text,
    frame_domain,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_fields import frame_noun
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_responses import (
    format_entity_details,
    format_field_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_filtering import (
    find_filtered_entities,
    has_filters,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import (
    account_status_grounded_entities,
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_text import (
    amount_reference_values,
    format_currency_amount,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_constants import (
    CONTEXT_READ_LIST_LIMIT,
)
from banking.presentation.i18n.renderer import render_message
from shared.types.planner import ContextFrameFollowupDecision


def format_explain_result_response(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision | None = None,
    *,
    text: str = "",
    locale: str = "en",
) -> str | None:
    if decision is not None:
        entities: list[ContextEntity] = []
        if decision.selection_index is not None:
            idx = decision.selection_index - 1
            if 0 <= idx < len(frame.items):
                entities = [frame.items[idx]]
        if not entities and has_filters(decision.filters):
            entities = find_filtered_entities(frame, decision.filters)
        if not entities and decision_target_text(decision):
            entities = find_matching_entities(frame, decision_target_text(decision))
        if not entities and text:
            entities = account_status_grounded_entities(frame, text)
        if not entities and frame.frame_type == ContextFrameType.ACCOUNT_LIST:
            pending_accounts = pending_account_entities(frame)
            if len(pending_accounts) == 1:
                entities = pending_accounts

        if len(entities) == 1 and frame.frame_type == ContextFrameType.ACCOUNT_LIST:
            explanation = format_account_status_explanation(entities[0])
            if explanation:
                return explanation
        if entities:
            field_response = format_field_response(frame, entities, decision_field_text(decision), locale=locale)
            return field_response or format_entity_details(frame, entities, locale=locale)

    count = len(frame.items)
    if count <= 0:
        return None

    noun = frame_noun(frame.frame_type, plural=count != 1, locale=locale)
    labels = [entity.label for entity in frame.items[:CONTEXT_READ_LIST_LIMIT] if entity.label]
    if not labels:
        return render_message(
            "context_frame.followup.explain_count",
            locale,
            {"count": count, "noun": noun},
        )

    label_text = ", ".join(labels)
    overflow = count - len(labels)
    suffix = (
        render_message("context_frame.followup.and_more_suffix", locale, {"count": overflow}) if overflow > 0 else ""
    )
    return render_message(
        "context_frame.followup.explain_labels",
        locale,
        {"count": count, "noun": noun, "labels": label_text, "suffix": suffix},
    )


def format_frame_clarification_response(frame: ContextFrame, *, locale: str = "en") -> str | None:
    domain = frame_domain(frame.frame_type)
    if domain == "beneficiary":
        return render_message("context_frame.followup.clarify_beneficiary", locale)
    if domain == "account":
        return render_message("context_frame.followup.clarify_account", locale)
    if domain == "query":
        return render_message("context_frame.followup.clarify_query", locale)
    if domain == "schedule":
        return render_message("context_frame.followup.clarify_schedule", locale)
    return render_message("context_frame.followup.clarify_items", locale)


def format_unclear_grounded_target_response(frame: ContextFrame, text: str, *, locale: str = "en") -> str | None:
    amount_refs = amount_reference_values(text)
    matches = find_matching_entities(frame, text)
    if len(matches) == 1:
        return format_entity_details(frame, matches, locale=locale)
    if len(matches) > 1:
        return format_entity_details(frame, matches, locale=locale)
    if amount_refs:
        amounts = ", ".join(format_currency_amount(value) for value in sorted(amount_refs))
        return render_message(
            "context_frame.followup.missing_entity",
            locale,
            {"target": amounts, "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
        )
    return None


def format_missing_amount_reference_response(
    frame: ContextFrame,
    text: str | None,
    *,
    locale: str = "en",
) -> str | None:
    amount_refs = amount_reference_values(text)
    if not amount_refs:
        return None
    amounts = ", ".join(format_currency_amount(value) for value in sorted(amount_refs))
    return render_message(
        "context_frame.followup.missing_entity",
        locale,
        {"target": amounts, "noun": frame_noun(frame.frame_type, plural=True, locale=locale)},
    )


__all__ = [
    "format_explain_result_response",
    "format_frame_clarification_response",
    "format_missing_amount_reference_response",
    "format_unclear_grounded_target_response",
]
