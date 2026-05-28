"""Scoped confirmation amount and transfer reference helpers."""

import re

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_text import (
    _normalize_recipient_match_text,
)

_SCOPED_CONFIRMATION_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?",
    re.IGNORECASE,
)


def _parse_scoped_confirmation_amount(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    match = _SCOPED_CONFIRMATION_AMOUNT_RE.search(text)
    if match is None:
        return None
    token = match.group(0)
    suffix = token[-1].lower() if token and token[-1].lower() in {"k", "h"} else ""
    number_part = token[:-1] if suffix else token
    normalized = re.sub(r"(?i)(?:₦|ngn)", "", number_part).strip()
    normalized = normalized.replace(",", "")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    if suffix == "k":
        amount *= 1000.0
    elif suffix == "h":
        amount *= 100.0
    return amount if amount > 0 else None


def _transfer_task_reference_matches(reference: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_reference = _normalize_recipient_match_text(reference)
    if not normalized_reference:
        return False

    for field in ("recipient_name", "recipient_resolved_name"):
        normalized_candidate = _normalize_recipient_match_text(str(payload.get(field) or ""))
        if not normalized_candidate:
            continue
        if re.search(rf"\b{re.escape(normalized_candidate)}\b", normalized_reference):
            return True
        if re.search(rf"\b{re.escape(normalized_reference)}\b", normalized_candidate):
            return True
    return False


def _transfer_task_amount(task: TaskSpec) -> float | None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    amount = payload.get("amount")
    if isinstance(amount, (int, float)) and amount > 0:
        return float(amount)

    confirmation = payload.get("confirmation")
    snapshot = confirmation.get("snapshot") if isinstance(confirmation, dict) else None
    snapshot_amount = snapshot.get("amount") if isinstance(snapshot, dict) else None
    if isinstance(snapshot_amount, (int, float)) and snapshot_amount > 0:
        return float(snapshot_amount)
    return None


__all__ = [
    "_parse_scoped_confirmation_amount",
    "_transfer_task_amount",
    "_transfer_task_reference_matches",
]
