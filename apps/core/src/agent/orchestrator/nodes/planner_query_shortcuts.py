"""Planner query-continuation shortcut helpers."""

import re

from apps.core.src.agent.orchestrator.models.domain import TaskSpec

QUERY_CONTINUATION_SHORTCUT_EXACT = {
    "more",
    "next",
    "show more",
    "next page",
    "show transactions",
    "show my transactions",
    "list transactions",
    "show them",
    "which ones",
    "details",
    "show details",
    "receipt",
    "issue",
    "report issue",
    "last month",
    "this month",
    "yesterday",
    "today",
    "only debits",
    "only credits",
}
QUERY_CONTINUATION_SHORTCUT_BLOCKLIST_PATTERNS = (
    r"\bbalance\b",
    r"\baccounts?\b",
    r"\bairtime\b",
    r"\bdata\b",
    r"\bbeneficiar(?:y|ies)\b",
    r"\bsupport\b",
    r"\bhelp\b",
    r"\bsend\b",
    r"\btransfer\b",
)


def _normalize_shortcut_message(message: str) -> str:
    return " ".join(message.lower().strip().split())


def _looks_like_explicit_query_continuation(message_text: str) -> bool:
    normalized = _normalize_shortcut_message(message_text)
    if not normalized:
        return False
    if normalized in QUERY_CONTINUATION_SHORTCUT_EXACT:
        return True
    if re.fullmatch(r"(only|just)\s+(credits?|debits?)", normalized):
        return True
    if re.fullmatch(r"(last|recent)\s+\d+", normalized):
        return True
    return False


def _is_query_continuation_blocked(message_text: str) -> bool:
    normalized = _normalize_shortcut_message(message_text)
    return any(re.search(pattern, normalized) for pattern in QUERY_CONTINUATION_SHORTCUT_BLOCKLIST_PATTERNS)


def _next_query_continuation_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = f"query_continuation_{idx}"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"query_continuation_{idx}"
    return task_id


__all__ = [
    "_is_query_continuation_blocked",
    "_looks_like_explicit_query_continuation",
    "_next_query_continuation_task_id",
    "_normalize_shortcut_message",
]
