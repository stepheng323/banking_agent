"""Shared text clipping and budgeted assembly for planner context rendering."""

from typing import Any

CONTEXT_USER_STATE_MAX_CHARS = 1200
ROUTER_CONTEXT_MAX_CHARS = 1600
ROUTER_CONTEXT_SECTION_MAX_CHARS = 320
INTERRUPT_CONTEXT_MAX_CHARS = 1800
INTERRUPT_CONTEXT_SECTION_MAX_CHARS = 500
QUOTED_REPLAY_CONTEXT_MAX_CHARS = 1800
QUOTED_REPLAY_CONTEXT_SECTION_MAX_CHARS = 500
PLANNER_CONTEXT_MAX_CHARS = 2800
PLANNER_MIN_SECTION_CHARS = 100
PLANNER_CONTEXT_SECTION_SEPARATOR = "\n\n"

QUERY_SESSION_CONTEXT_HEADER = "Active Query Session: The user recently viewed transaction results."
QUERY_SESSION_CONTEXT_GUIDANCE = (
    "- Continuation/refinement/analytics on shown transactions stay in query "
    "(for example: 'more', 'next', 'details', 'receipt', 'any credits?', "
    "'any debit?', 'only debits', 'last month', 'how much did I spend?', 'total spending').\n"
    "- If query is waiting for clarification, short answers that complete the missing query detail stay in query "
    "(for example: 'last 3 days', 'today', 'this month').\n"
    "- Fresh transaction-history asks are also query tasks.\n"
    "- Data questions are NOT conversational questions. Always route them as query tasks.\n"
    "- Balance/account-status asks are NOT query continuation; route them to account tasks."
)


def _clip_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 16:
        return value[:max_chars]
    return value[: max_chars - 15].rstrip() + " ...[truncated]"


def _build_query_session_context(
    summary_text: str | None,
    pending_clarification: dict[str, Any] | None = None,
) -> str:
    summary_snippet = ""
    if summary_text:
        summary_snippet = f' Last summary: "{_clip_text(summary_text, 180)}".'
    clarification_snippet = ""
    if pending_clarification:
        original_query = str(pending_clarification.get("original_query") or "").strip()
        resolver_message = str(pending_clarification.get("resolver_message") or "").strip()
        clarification_parts: list[str] = []
        if original_query:
            clarification_parts.append(f'Unresolved query: "{_clip_text(original_query, 120)}".')
        if resolver_message:
            clarification_parts.append(f'Waiting for: "{_clip_text(resolver_message, 120)}".')
        if clarification_parts:
            clarification_snippet = " " + " ".join(clarification_parts)
    return f"{QUERY_SESSION_CONTEXT_HEADER}{summary_snippet}{clarification_snippet}\n{QUERY_SESSION_CONTEXT_GUIDANCE}"


def _assemble_planner_context(
    sections: list[tuple[str, str]],
    *,
    max_chars: int = PLANNER_CONTEXT_MAX_CHARS,
) -> tuple[str, list[str], list[str], list[str]]:
    assembled: list[str] = []
    included: list[str] = []
    clipped: list[str] = []
    dropped: list[str] = []
    current_len = 0

    for name, section in sections:
        section_text = section.strip()
        if not section_text:
            continue

        separator_len = len(PLANNER_CONTEXT_SECTION_SEPARATOR) if assembled else 0
        remaining = max_chars - current_len - separator_len
        if remaining <= 0:
            dropped.append(name)
            continue

        next_text = section_text
        if len(next_text) > remaining:
            if remaining < PLANNER_MIN_SECTION_CHARS:
                dropped.append(name)
                continue
            next_text = _clip_text(next_text, remaining)
            clipped.append(name)

        assembled.append(next_text)
        included.append(name)
        current_len += separator_len + len(next_text)

    if not assembled:
        return "None", included, clipped, dropped
    return PLANNER_CONTEXT_SECTION_SEPARATOR.join(assembled), included, clipped, dropped


__all__ = [
    "CONTEXT_USER_STATE_MAX_CHARS",
    "INTERRUPT_CONTEXT_MAX_CHARS",
    "INTERRUPT_CONTEXT_SECTION_MAX_CHARS",
    "PLANNER_CONTEXT_MAX_CHARS",
    "PLANNER_CONTEXT_SECTION_SEPARATOR",
    "PLANNER_MIN_SECTION_CHARS",
    "QUERY_SESSION_CONTEXT_GUIDANCE",
    "QUERY_SESSION_CONTEXT_HEADER",
    "QUOTED_REPLAY_CONTEXT_MAX_CHARS",
    "QUOTED_REPLAY_CONTEXT_SECTION_MAX_CHARS",
    "ROUTER_CONTEXT_MAX_CHARS",
    "ROUTER_CONTEXT_SECTION_MAX_CHARS",
    "_assemble_planner_context",
    "_build_query_session_context",
    "_clip_text",
]
