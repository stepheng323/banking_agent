"""Explanation and clarification responses for context frames."""

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_account_status import (
    format_account_status_explanation,
    pending_account_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_decisions import (
    decision_field_text,
    decision_target_text,
    frame_domain,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_detail_fields import frame_noun
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_detail_responses import (
    format_entity_details,
    format_field_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_filtering import (
    find_filtered_entities,
    has_filters,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_search import (
    account_status_grounded_entities,
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_text import (
    amount_reference_values,
    format_currency_amount,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import (
    CONTEXT_READ_LIST_LIMIT,
)
from shared.types.planner import ContextFrameFollowupDecision


def format_explain_result_response(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision | None = None,
    *,
    text: str = "",
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
            field_response = format_field_response(frame, entities, decision_field_text(decision))
            return field_response or format_entity_details(frame, entities)

    count = len(frame.items)
    if count <= 0:
        return None

    noun = frame_noun(frame.frame_type, plural=count != 1)
    labels = [entity.label for entity in frame.items[:CONTEXT_READ_LIST_LIMIT] if entity.label]
    if not labels:
        return f"I showed {count} {noun} from the last result."

    label_text = ", ".join(labels)
    overflow = count - len(labels)
    suffix = f", and {overflow} more" if overflow > 0 else ""
    return f"I showed {count} {noun}: {label_text}{suffix}."


def format_frame_clarification_response(frame: ContextFrame) -> str | None:
    domain = frame_domain(frame.frame_type)
    if domain == "beneficiary":
        return "Are you asking about the saved beneficiaries I just showed?"
    if domain == "account":
        return "Are you asking about the linked accounts I just showed?"
    if domain == "query":
        return "Are you asking about the result I just showed?"
    if domain == "schedule":
        return "Are you asking about the scheduled transactions I just showed?"
    return "Are you asking about the items I just showed?"


def format_unclear_grounded_target_response(frame: ContextFrame, text: str) -> str | None:
    amount_refs = amount_reference_values(text)
    matches = find_matching_entities(frame, text)
    if len(matches) == 1:
        return format_entity_details(frame, matches)
    if len(matches) > 1:
        return format_entity_details(frame, matches)
    if amount_refs:
        amounts = ", ".join(format_currency_amount(value) for value in sorted(amount_refs))
        return f"I don't see {amounts} in the {frame_noun(frame.frame_type, plural=True)} I showed."
    return None


def format_missing_amount_reference_response(frame: ContextFrame, text: str | None) -> str | None:
    amount_refs = amount_reference_values(text)
    if not amount_refs:
        return None
    amounts = ", ".join(format_currency_amount(value) for value in sorted(amount_refs))
    return f"I don't see {amounts} in the {frame_noun(frame.frame_type, plural=True)} I showed."


__all__ = [
    "format_explain_result_response",
    "format_frame_clarification_response",
    "format_missing_amount_reference_response",
    "format_unclear_grounded_target_response",
]
