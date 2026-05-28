"""Resolve referential phrases against short-term referent memory."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.context.referents.models import (
    ReferentMemoryItem,
    ReferentResolution,
    ReferentType,
)
from apps.chat.src.agent.orchestrator.context.referents.store import prune_referent_memory

_RECIPIENT_REFERENCE_RE = re.compile(
    r"\b(?:him|her|them|that\s+(?:person|recipient)|this\s+(?:person|recipient)|"
    r"that\s+(?:guy|babe|customer)|same\s+(?:person|recipient|guy|babe|customer)|"
    r"previous\s+(?:person|recipient|guy|babe|customer)|the\s+previous\s+one|"
    r"(?:send|transfer|pay)\s+am\b|(?:to|for)\s+am\b)\b",
    re.IGNORECASE,
)
_PHONE_REFERENCE_RE = re.compile(
    r"\b(?:that\s+(?:number|line)|this\s+(?:number|line)|same\s+(?:number|line)|"
    r"previous\s+(?:number|line)|that\s+sim|same\s+sim|"
    r"(?:buy|purchase|top\s*up)\s+(?:airtime|data)\s+(?:for\s+)?am\b)\b",
    re.IGNORECASE,
)
_AMOUNT_REFERENCE_RE = re.compile(
    r"\b(?:same\s+(?:amount|money|thing)|that\s+(?:amount|money)|this\s+(?:amount|money)|"
    r"previous\s+(?:amount|money)|same\s+again|do\s+(?:it\s+)?again|send\s+(?:it\s+)?again|"
    r"buy\s+(?:it\s+)?again|purchase\s+(?:it\s+)?again|repeat(?:\s+(?:it|that))?|again)\b",
    re.IGNORECASE,
)
_SOURCE_ACCOUNT_REFERENCE_RE = re.compile(
    r"\b(?:same\s+(?:account|bank|source|debit\s+account)|that\s+(?:account|bank|source|debit\s+account)|"
    r"this\s+(?:account|bank|source|debit\s+account)|previous\s+(?:account|bank|source|debit\s+account)|"
    r"(?:from|using|use|with|debit(?:ing)?|charge)\s+(?:the\s+)?same\s+(?:account|bank))\b",
    re.IGNORECASE,
)
_DATA_PLAN_REFERENCE_RE = re.compile(
    r"\b(?:that\s+(?:data\s+)?plan|this\s+(?:data\s+)?plan|same\s+(?:data\s+)?plan|"
    r"that\s+bundle|this\s+bundle|the\s+(?:first|second|third|monthly|weekly|daily)\s+one|"
    r"(?:option|number|#)\s*\d{1,2}|"
    r"(?:buy|get|purchase)\s+(?:it|that|that\s+one|(?:the\s+)?(?:monthly|weekly|daily)\s+one|"
    r"option\s+\d{1,2}|number\s+\d{1,2}|the\s+plan|the\s+bundle))\b",
    re.IGNORECASE,
)
_DATA_PLAN_OPTION_RE = re.compile(
    r"\b(?:option|number|#)\s*(\d{1,2})\b|\b(?:the\s+)?(first|second|third)\s+one\b",
    re.IGNORECASE,
)
_DATA_PLAN_VALIDITY_WORDS = {"daily": 1, "weekly": 7, "monthly": 30}
_EXPLICIT_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)\s*\d|"
    r"\b\d[\d,]*(?:\.\d+)?\s*[kKhH]\b|"
    r"\b(?:send|transfer|pay|remit|buy|purchase|top\s*up)\s+"
    r"(?:me\s+|him\s+|her\s+|them\s+|am\s+|airtime\s+|data\s+)?"
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?(?:\s*[kKhH])?\b",
    re.IGNORECASE,
)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None or value == "":
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _resolution_dedupe_key(item: ReferentMemoryItem) -> str:
    data = item.data
    key = (
        item.entity_id
        or _first_text(
            data.get("beneficiary_id"),
            data.get("id"),
            data.get("recipient_account"),
            data.get("account_number"),
            data.get("phone"),
            data.get("recipient_phone"),
            data.get("target_phone"),
            data.get("transaction_id"),
            data.get("reference"),
            data.get("plan_code"),
            data.get("item_code"),
            data.get("amount"),
            item.label,
        )
        or ""
    )
    return key or f"{item.referent_type}:{item.label or id(item)}"


def _prefer_resolution_item(existing: ReferentMemoryItem, candidate: ReferentMemoryItem) -> ReferentMemoryItem:
    if candidate.confidence != existing.confidence:
        return candidate if candidate.confidence > existing.confidence else existing
    if candidate.referent_type == "recipient" and existing.referent_type == "beneficiary":
        return candidate
    if candidate.created_at_ts > existing.created_at_ts:
        return candidate
    return existing


def _resolve_candidates(state: Any, referent_types: set[ReferentType]) -> list[ReferentMemoryItem]:
    memory = prune_referent_memory(state)
    candidates = [item for item in memory.items if item.referent_type in referent_types]
    deduped: dict[str, ReferentMemoryItem] = {}
    for item in candidates:
        key = _resolution_dedupe_key(item)
        existing = deduped.get(key)
        deduped[key] = item if existing is None else _prefer_resolution_item(existing, item)
    return sorted(
        deduped.values(),
        key=lambda item: (item.confidence, item.created_at_ts),
        reverse=True,
    )


def _resolve_reference(
    state: Any,
    *,
    text: str | None,
    referent_type: ReferentType,
    candidate_types: set[ReferentType],
    pattern: re.Pattern[str],
) -> ReferentResolution:
    if not pattern.search(text or ""):
        return ReferentResolution(status="none", referent_type=referent_type, reason="no_reference_phrase")
    candidates = _resolve_candidates(state, candidate_types)
    if not candidates:
        return ReferentResolution(status="none", referent_type=referent_type, reason="no_candidates")
    top = candidates[0]
    if len(candidates) == 1:
        if top.confidence < 0.75:
            return ReferentResolution(status="none", referent_type=referent_type, reason="low_confidence")
        return ReferentResolution(status="resolved", referent_type=referent_type, item=top)
    second = candidates[1]
    if top.confidence >= 0.7 and top.confidence - second.confidence < 0.15:
        return ReferentResolution(
            status="ambiguous",
            referent_type=referent_type,
            candidates=candidates[:5],
            reason="multiple_candidates",
        )
    if top.confidence < 0.75:
        return ReferentResolution(status="none", referent_type=referent_type, reason="low_confidence")
    return ReferentResolution(status="resolved", referent_type=referent_type, item=top)


def resolve_recipient_reference(state: Any, text: str | None) -> ReferentResolution:
    return _resolve_reference(
        state,
        text=text,
        referent_type="recipient",
        candidate_types={"beneficiary", "recipient"},
        pattern=_RECIPIENT_REFERENCE_RE,
    )


def resolve_phone_reference(state: Any, text: str | None) -> ReferentResolution:
    return _resolve_reference(
        state,
        text=text,
        referent_type="phone",
        candidate_types={"phone"},
        pattern=_PHONE_REFERENCE_RE,
    )


def resolve_amount_reference(state: Any, text: str | None) -> ReferentResolution:
    if _EXPLICIT_AMOUNT_RE.search(text or ""):
        return ReferentResolution(status="none", referent_type="amount", reason="explicit_amount_present")
    return _resolve_reference(
        state,
        text=text,
        referent_type="amount",
        candidate_types={"amount"},
        pattern=_AMOUNT_REFERENCE_RE,
    )


def resolve_source_account_reference(state: Any, text: str | None) -> ReferentResolution:
    return _resolve_reference(
        state,
        text=text,
        referent_type="source_account",
        candidate_types={"source_account"},
        pattern=_SOURCE_ACCOUNT_REFERENCE_RE,
    )


def resolve_data_plan_reference(state: Any, text: str | None) -> ReferentResolution:
    if not _DATA_PLAN_REFERENCE_RE.search(text or ""):
        return ReferentResolution(status="none", referent_type="data_plan", reason="no_reference_phrase")
    candidates = _resolve_candidates(state, {"data_plan"})
    if not candidates:
        return ReferentResolution(status="none", referent_type="data_plan", reason="no_candidates")

    option_match = _DATA_PLAN_OPTION_RE.search(text or "")
    if option_match:
        ordinal = {"first": 1, "second": 2, "third": 3}.get((option_match.group(2) or "").lower())
        selected_index = int(option_match.group(1) or ordinal or 0)
        if selected_index:
            for item in candidates:
                try:
                    item_index = int(item.data.get("index") or 0)
                except (TypeError, ValueError):
                    item_index = 0
                if item_index == selected_index:
                    return ReferentResolution(status="resolved", referent_type="data_plan", item=item)
            if len(candidates) >= selected_index:
                return ReferentResolution(
                    status="resolved",
                    referent_type="data_plan",
                    item=candidates[selected_index - 1],
                )

    normalized = (text or "").lower()
    for word, days in _DATA_PLAN_VALIDITY_WORDS.items():
        if word not in normalized:
            continue
        matches = [item for item in candidates if int(item.data.get("validity_days") or 0) == days]
        if len(matches) == 1:
            return ReferentResolution(status="resolved", referent_type="data_plan", item=matches[0])
        if len(matches) > 1:
            return ReferentResolution(
                status="ambiguous",
                referent_type="data_plan",
                candidates=matches[:5],
                reason="multiple_validity_matches",
            )

    return _resolve_reference(
        state,
        text=text,
        referent_type="data_plan",
        candidate_types={"data_plan"},
        pattern=_DATA_PLAN_REFERENCE_RE,
    )


def build_resolved_referents(state: Any, text: str | None) -> dict[str, Any]:
    """Resolve all known reference classes for the current text."""
    results = {
        "recipient": resolve_recipient_reference(state, text),
        "phone": resolve_phone_reference(state, text),
        "amount": resolve_amount_reference(state, text),
        "source_account": resolve_source_account_reference(state, text),
        "data_plan": resolve_data_plan_reference(state, text),
    }
    return {
        key: value.model_dump(mode="json", exclude_none=True)
        for key, value in results.items()
        if value.status != "none"
    }
