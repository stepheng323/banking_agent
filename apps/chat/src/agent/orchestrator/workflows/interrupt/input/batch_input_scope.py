"""Deterministic, task-scoped attribution for guided batch-input replies."""

from __future__ import annotations

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.beneficiaries.services.selection import match_beneficiary_candidate_selection
from banking.transfers.extraction.parsers import parse_amount_input

_AMOUNT_TOKEN_RE = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\b", re.IGNORECASE)
_COMBINED_FILLER_RE = re.compile(r"\b(?:and|plus|with|airtime|for|my|line)\b", re.IGNORECASE)


def _amount_reply(text: str) -> str | None:
    stripped = text.strip()
    if parse_amount_input(stripped) is not None:
        has_amount_marker = bool(re.search(r"(?:₦|ngn|naira|[kKhH]\b)", stripped, re.IGNORECASE))
        if has_amount_marker or not stripped.isdigit() or int(stripped) > 10:
            return stripped
    match = _AMOUNT_TOKEN_RE.search(text)
    if match is None:
        return None
    candidate = match.group(0).strip()
    if parse_amount_input(candidate) is None:
        return None
    has_amount_marker = bool(re.search(r"(?:₦|ngn|naira|[kKhH]\b)", candidate, re.IGNORECASE))
    return candidate if has_amount_marker or not candidate.isdigit() or int(candidate) > 10 else None


def _selection_reply(text: str, candidates: list[dict[str, Any]]) -> str | None:
    stripped = text.strip()
    if stripped.isdigit() and 1 <= int(stripped) <= len(candidates):
        return stripped
    without_amount = _AMOUNT_TOKEN_RE.sub(" ", stripped)
    without_filler = _COMBINED_FILLER_RE.sub(" ", without_amount)
    normalized = " ".join(without_filler.split())
    if normalized and match_beneficiary_candidate_selection(normalized, candidates):
        return normalized
    if match_beneficiary_candidate_selection(stripped, candidates):
        return stripped
    return None


def resolve_batch_input_message_scope(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> dict[str, str] | None:
    """Return the current reply scoped to the slots it explicitly fills.

    An empty mapping is intentionally distinct from ``None``: it means a
    guided contract exists but this reply does not safely fill any slot.
    """
    contract = getattr(interrupt, "batch_input", None)
    if getattr(interrupt, "kind", None) != "input" or contract is None:
        return None

    scoped: dict[str, str] = {}
    for slot in contract.slots:
        task = state.tasks.get(slot.task_id)
        if task is None:
            continue
        if slot.kind == "selection" and slot.field == "beneficiary_id":
            raw_candidates = task.payload.get("beneficiary_candidates")
            candidates = (
                [item for item in raw_candidates if isinstance(item, dict)]
                if isinstance(raw_candidates, list)
                else []
            )
            if candidates and (selection := _selection_reply(text, candidates)):
                scoped[slot.task_id] = selection
        elif slot.kind == "amount" and (amount := _amount_reply(text)):
            scoped[slot.task_id] = amount

    return scoped


__all__ = ["resolve_batch_input_message_scope"]
