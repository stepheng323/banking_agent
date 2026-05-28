"""Action inference helpers for interrupt switch extraction."""

import re
from typing import Any

_TRANSFER_CANCEL_SCHEDULE_RE = re.compile(
    r"\b(cancel|stop|delete|remove)\b[\w\s]{0,40}\b(schedule|scheduled|recurring|auto)\b",
    re.IGNORECASE,
)
_TRANSFER_EDIT_SCHEDULE_RE = re.compile(
    r"\b(change|edit|update|move|shift|reschedule)\b[\w\s]{0,60}\b(schedule|scheduled|recurring|auto|transfer|airtime|data)\b"
    r"|\b(schedule|scheduled|recurring|auto)\b[\w\s]{0,60}\b(change|edit|update|move|shift|reschedule)\b",
    re.IGNORECASE,
)
_TRANSFER_LIST_SCHEDULE_RE = re.compile(
    r"\b(show|list|find|view|check)\b[\w\s]{0,40}\b(schedule|scheduled|recurring|auto)\b",
    re.IGNORECASE,
)
_TRANSFER_RECURRING_RE = re.compile(r"\b(every|daily|weekly|monthly|recurring)\b", re.IGNORECASE)
_TRANSFER_SCHEDULE_RE = re.compile(
    r"\b(schedule|scheduled|tomorrow|tommorow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)


def _feature_tokens(requested_features: list[Any] | None) -> set[str]:
    tokens: set[str] = set()
    for feature in requested_features or []:
        if hasattr(feature, "value"):
            tokens.add(str(feature.value).strip().upper())
        else:
            tokens.add(str(feature).strip().upper())
    return {token for token in tokens if token}


def _infer_transfer_switch_action(text: str, feature_tokens: set[str]) -> str:
    if _TRANSFER_CANCEL_SCHEDULE_RE.search(text):
        return "cancel_scheduled_transfer"
    if _TRANSFER_EDIT_SCHEDULE_RE.search(text):
        return "edit_scheduled_transaction"
    if _TRANSFER_LIST_SCHEDULE_RE.search(text):
        return "list_scheduled_transactions"
    if "RECURRING" in feature_tokens or _TRANSFER_RECURRING_RE.search(text):
        return "recurring_transfer"
    if "SCHEDULED" in feature_tokens or _TRANSFER_SCHEDULE_RE.search(text):
        return "schedule_transfer"
    return "send_money"


def _map_schedule_action_for_domain(action: str, target_intent: str) -> str:
    if target_intent == "airtime" and action in {"schedule_transfer", "recurring_transfer"}:
        return "recurring_airtime" if action == "recurring_transfer" else "schedule_airtime"
    if target_intent == "data" and action in {"schedule_transfer", "recurring_transfer"}:
        return "recurring_data" if action == "recurring_transfer" else "schedule_data"
    return action


__all__ = [
    "_feature_tokens",
    "_infer_transfer_switch_action",
    "_map_schedule_action_for_domain",
]
