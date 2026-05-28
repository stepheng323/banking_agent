"""Deterministic answers derived from grounded query frames."""

from __future__ import annotations

from apps.chat.src.agent.workers.query.grounding.frames import (
    can_compare_frames,
    format_period_label,
    resolve_query_frames,
)
from apps.chat.src.agent.workers.query.models.domain import QueryFrame
from shared.i18n.message_keys import MessageKey
from shared.i18n.renderer import render_message


def build_memory_answer(
    *,
    query_frames: list[QueryFrame],
    frame_ids: list[str] | None,
    operation: str | None,
    language: str,
) -> str | None:
    """Build a deterministic answer directly from stored query frame facts."""
    if operation != "compare_frames":
        return None

    frames = resolve_query_frames(query_frames, frame_ids)
    if len(frames) < 2 or not can_compare_frames(frames[0], frames[1]):
        return None

    current_frame, comparison_frame = frames[0], frames[1]
    current_facts = current_frame.facts
    comparison_facts = comparison_frame.facts

    if current_facts.metric_kind != "amount" or comparison_facts.metric_kind != "amount":
        return None
    if current_facts.direction != "debit" or comparison_facts.direction != "debit":
        return None
    if current_facts.amount is None or comparison_facts.amount is None:
        return None

    current_label = format_period_label(current_frame.query_contract.time_range)
    comparison_label = format_period_label(comparison_frame.query_contract.time_range)
    if current_label is None or comparison_label is None:
        return None

    spending_change = current_facts.amount - comparison_facts.amount
    if spending_change == 0:
        return render_message(
            "query.time_comparison.same_spending",
            language,
            {"current_label": current_label, "comparison_label": comparison_label},
        )

    verb_key: MessageKey = (
        "query.time_comparison.verb_spent_more" if spending_change > 0 else "query.time_comparison.verb_spent_less"
    )
    return render_message(
        "query.time_comparison.summary",
        language,
        {
            "verb": render_message(verb_key, language),
            "amount": f"{abs(spending_change):,.0f}",
            "current_label": current_label,
            "comparison_label": comparison_label,
        },
    )
