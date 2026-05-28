"""Data-plan input slot shortcut patterns."""

import re
from typing import Any

_INPUT_DATA_PLAN_SELECTION_REPLY_RE = re.compile(
    r"^(?:option\s*)?(?P<index>[1-9]\d*)[.!?]?$",
    re.IGNORECASE,
)
_INPUT_DATA_PLAN_BUDGET_REPLY_RE = re.compile(
    r"^(?:(?:under|within|below|around|about|max(?:imum)?|for)\s+)?"
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?\s*(?:naira|ngn)?\s*[.!?]?$",
    re.IGNORECASE,
)
_INPUT_DATA_PLAN_SIZE_REPLY_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:gb|g|mb)\s*[.!?]?$",
    re.IGNORECASE,
)
_INPUT_DATA_PLAN_VALIDITY_REPLY_RE = re.compile(
    r"^(?:daily|weekly|monthly|night|weekend)(?:\s+(?:plan|bundle|one))?\s*[.!?]?$",
    re.IGNORECASE,
)
_INPUT_DATA_PLAN_PREFERENCE_REPLY_RE = re.compile(
    r"^(?:best|cheapest|most\s+data|highest\s+data|longest\s+validity|best\s+value)\s*[.!?]?$",
    re.IGNORECASE,
)


def _looks_like_data_plan_selection_reply(text: str, active_task: Any) -> bool:
    match = _INPUT_DATA_PLAN_SELECTION_REPLY_RE.fullmatch(text.strip())
    if not match:
        return False
    selected_index = int(match.group("index"))
    if selected_index <= 0:
        return False

    payload = active_task.payload if active_task is not None and isinstance(active_task.payload, dict) else {}
    raw_candidates = payload.get("data_plan_candidates")
    candidates = (
        [candidate for candidate in raw_candidates if isinstance(candidate, dict)]
        if isinstance(raw_candidates, list)
        else []
    )
    if not candidates:
        return selected_index <= 3
    for candidate in candidates:
        try:
            candidate_index = int(candidate.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if candidate_index == selected_index:
            return True
    return False


def _looks_like_data_plan_preference_reply(text: str) -> bool:
    stripped_text = text.strip()
    if not stripped_text or "?" in stripped_text:
        return False
    return bool(
        _INPUT_DATA_PLAN_BUDGET_REPLY_RE.fullmatch(stripped_text)
        or _INPUT_DATA_PLAN_SIZE_REPLY_RE.fullmatch(stripped_text)
        or _INPUT_DATA_PLAN_VALIDITY_REPLY_RE.fullmatch(stripped_text)
        or _INPUT_DATA_PLAN_PREFERENCE_REPLY_RE.fullmatch(stripped_text)
    )


__all__ = [
    "_looks_like_data_plan_preference_reply",
    "_looks_like_data_plan_selection_reply",
]
