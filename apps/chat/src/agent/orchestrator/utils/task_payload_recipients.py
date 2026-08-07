"""Recipient grounding helpers for planner task payloads."""

from __future__ import annotations

import re
from typing import Any

from apps.chat.src.agent.orchestrator.utils.task_payload_schedule import SCHEDULE_DATE_PATTERN
from shared.utils.bank_aliases import get_bank_search_terms
from shared.utils.sanitize import normalize_bank_account_number

_TRANSFER_VERB_TOKENS = {"send", "transfer", "pay", "remit"}
_RECIPIENT_NOISE_TOKENS = _TRANSFER_VERB_TOKENS | {"to", "for", "money", "cash", "funds", "s"}
_RECIPIENT_SEGMENT_BOUNDARY = re.compile(r"\b(?:but|then|from|using|with|via|through|while)\b")
_RECIPIENT_SCHEDULE_SUFFIX_RE = re.compile(rf"\s+(?:by\s+)?{SCHEDULE_DATE_PATTERN}\b.*$", re.IGNORECASE)


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _digits_only(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


def recipient_account_grounded_in_user_text(recipient_account: str | None, user_text: str) -> bool:
    account_digits = _digits_only(recipient_account)
    if len(account_digits) < 10:
        return False
    text_digits = _digits_only(user_text)
    if not text_digits:
        return False
    if account_digits in text_digits:
        return True
    normalized_account = normalize_bank_account_number(account_digits) or ""
    return bool(normalized_account and normalized_account in text_digits)


def recipient_bank_grounded_in_user_text(recipient_bank_name: str | None, user_text: str) -> bool:
    norm_bank = _normalize_text(recipient_bank_name)
    norm_text = _normalize_text(user_text)
    if not norm_bank or not norm_text:
        return False
    if re.search(rf"\b{re.escape(norm_bank)}\b", norm_text):
        return True

    base_bank = re.sub(r"\bbank\b", "", norm_bank).strip()
    if base_bank and re.search(rf"\b{re.escape(base_bank)}\b", norm_text):
        return True

    for term in get_bank_search_terms(recipient_bank_name or ""):
        norm_term = _normalize_text(term)
        if not norm_term:
            continue
        if re.search(rf"\b{re.escape(norm_term)}\b", norm_text):
            return True
    return False


def _is_plausible_recipient_candidate(candidate: str | None) -> bool:
    norm_candidate = _normalize_text(candidate)
    if not norm_candidate:
        return False
    if norm_candidate.isdigit():
        return False
    tokens = [token for token in norm_candidate.split() if token]
    if any(re.fullmatch(r"\d+(?:k|m)?", token) for token in tokens):
        return False
    return not all(token in _RECIPIENT_NOISE_TOKENS for token in tokens)


def strip_recipient_schedule_suffix(value: str | None) -> str | None:
    if not value:
        return value
    stripped = _RECIPIENT_SCHEDULE_SUFFIX_RE.sub("", value).strip(" \t\r\n,.;:!?")
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped or None


def recipient_grounded_in_user_text(recipient: str | None, user_text: str) -> bool:
    """Return True if planner recipient is clearly present in user's original message."""
    norm_recipient = _normalize_text(recipient)
    norm_text = _normalize_text(user_text)
    if not norm_recipient or not norm_text:
        return True
    if not _is_plausible_recipient_candidate(norm_recipient):
        return False
    return norm_recipient in norm_text


def clear_external_recipient_bindings_for_self(payload: dict[str, Any]) -> None:
    """Keep a typed own-account transfer from carrying a sibling recipient."""
    for key in (
        "beneficiary_id",
        "beneficiary_candidates",
        "referent_recipient_candidates",
        "recipient",
        "recipient_reference",
        # A sibling account-number resolution can otherwise make the
        # pipeline short-circuit before it reaches linked-account resolution.
        # The own-account bank scope is retained; the concrete destination is
        # always re-resolved from the user's linked accounts.
        "recipient_account",
        "recipient_account_number",
        "recipient_bank_code",
        "recipient_bank_code_provider",
        "resolved_from_saved_beneficiary",
        "recipient_resolution_provider",
        "recipient_resolution_mode",
        "recipient_resolved_name",
        "recipient_name",
    ):
        payload.pop(key, None)


def derive_recipients_from_user_text(user_text: str) -> list[str]:
    """Derive ordered recipient candidates from a transfer utterance."""
    lowered_text = re.sub(r"([a-z])['’]s\b", r"\1", user_text.lower())
    simplified_text = re.sub(r"[^a-z0-9,\s]+", " ", lowered_text)
    simplified_text = re.sub(r"\s+", " ", simplified_text).strip()
    if not simplified_text:
        return []

    match = re.search(r"\b(?:to|for|si|ga|zuwa)\b\s+(.+)", simplified_text)
    if not match:
        match = re.search(r"\b(?:between|btw)\b\s+(.+)", simplified_text)
    if not match:
        return []

    segment = match.group(1).strip()
    if not segment:
        return []

    segment = _RECIPIENT_SEGMENT_BOUNDARY.split(segment, maxsplit=1)[0].strip()
    segment = strip_recipient_schedule_suffix(segment) or ""
    segment = re.sub(r"\band\s+to\b", " and ", segment)
    if not segment:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for raw_part in re.split(r"\s*,\s*|\s+\band\b\s+", segment):
        part = re.sub(r"^(?:to|for|si|ga|zuwa)\s+", "", raw_part).strip()
        normalized_part = _normalize_text(part)
        if not _is_plausible_recipient_candidate(normalized_part):
            continue
        key = normalized_part
        if key and key not in seen:
            seen.add(key)
            candidates.append(key)
    return candidates


def derive_recipient_from_user_text(planned_recipient: str | None, user_text: str) -> str | None:
    """Derive a safe recipient token from user text when planner over-expands names."""
    norm_planned = _normalize_text(planned_recipient)
    norm_text = _normalize_text(user_text)
    if not norm_text:
        return None

    recipient_candidates = derive_recipients_from_user_text(user_text)
    if recipient_candidates:
        if len(recipient_candidates) == 1:
            return recipient_candidates[0]
        planned_tokens = {token for token in norm_planned.split() if token}
        best_candidate: str | None = None
        best_score = 0
        for candidate in recipient_candidates:
            candidate_tokens = {token for token in _normalize_text(candidate).split() if token}
            if not candidate_tokens:
                continue
            overlap = len(planned_tokens & candidate_tokens)
            if overlap > best_score:
                best_score = overlap
                best_candidate = candidate
        if best_candidate is not None and best_score > 0:
            return best_candidate
        return None

    planned_token_list = [token for token in norm_planned.split() if token]
    text_tokens = {token for token in norm_text.split() if token}
    for token in planned_token_list:
        if token in text_tokens and _is_plausible_recipient_candidate(token):
            return token

    return None
