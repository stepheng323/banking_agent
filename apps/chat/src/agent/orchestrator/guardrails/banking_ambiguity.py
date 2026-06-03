"""Deterministic guardrails for malformed banking-coded user turns."""

from __future__ import annotations

import re
from typing import Literal

from banking.intent.routing_signals import looks_like_explicit_transaction_query_shape
from banking.presentation.i18n.renderer import render_message

AmbiguousBankingDomain = Literal["transfer", "airtime", "data", "support", "account_query"]

_TRANSFER_SELF_DIRECTED_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:send|transfer|pay|remit|split)\s+me\b",
    re.IGNORECASE,
)
_AIRTIME_SELF_DIRECTED_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:buy|recharge|top\s*up|topup|load)\s+me\b.*\bairtime\b",
    re.IGNORECASE,
)
_DATA_SELF_DIRECTED_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:buy|get|send)\s+me\b.*\b(?:data|bundle)\b",
    re.IGNORECASE,
)
_RECHARGE_ME_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*recharge\s+me\b",
    re.IGNORECASE,
)
_SUPPORT_AMBIGUOUS_RE = re.compile(
    r"\b(?:reverse|refund|reversal|receipt|proof\s+of\s+payment|failed|debited|chargeback)\b",
    re.IGNORECASE,
)
_SUPPORT_UNGROUNDED_SELF_RE = re.compile(r"\b(?:me|that|this)\b", re.IGNORECASE)
_SUPPORT_REQUEST_VERB_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:send|show|get|check|issue|provide|give|need|want)\b",
    re.IGNORECASE,
)
_ACCOUNT_QUERY_MIXED_RE = re.compile(
    r"\b(?:balance|account|transactions?|history|statement|receipt)\b.*\b(?:transfer|send|pay|payment)\b|"
    r"\b(?:transfer|send|pay|payment)\b.*\b(?:balance|account|transactions?|history|statement|receipt)\b",
    re.IGNORECASE,
)
_MULTI_CLAUSE_MARKERS = (" and ", " & ", " then ", ",")
_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*\d", re.IGNORECASE)


def classify_banking_coded_ambiguity(text: str | None) -> AmbiguousBankingDomain | None:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip("?.!,")
    if not normalized:
        return None

    if _TRANSFER_SELF_DIRECTED_RE.search(normalized):
        return "transfer"
    if _DATA_SELF_DIRECTED_RE.search(normalized):
        return None
    if _AIRTIME_SELF_DIRECTED_RE.search(normalized):
        if _AMOUNT_RE.search(normalized):
            return None
        return "airtime"
    if _RECHARGE_ME_RE.search(normalized):
        return "airtime"
    if _SUPPORT_AMBIGUOUS_RE.search(normalized) and (
        _SUPPORT_UNGROUNDED_SELF_RE.search(normalized) or _SUPPORT_REQUEST_VERB_RE.search(normalized)
    ):
        if looks_like_explicit_transaction_query_shape(normalized):
            return None
        return "support"
    if (
        _ACCOUNT_QUERY_MIXED_RE.search(normalized)
        and not any(marker in normalized for marker in _MULTI_CLAUSE_MARKERS)
        and not _AMOUNT_RE.search(normalized)
    ):
        return "account_query"
    return None


def render_banking_coded_ambiguity_prompt(text: str | None, *, locale: str) -> str:
    domain = classify_banking_coded_ambiguity(text)
    if domain == "transfer":
        return render_message("orchestrator.ambiguity.transfer_recipient", locale)
    if domain == "airtime":
        return render_message("orchestrator.ambiguity.airtime_recipient", locale)
    if domain == "data":
        return render_message("orchestrator.ambiguity.data_recipient", locale)
    if domain == "support":
        return render_message("orchestrator.ambiguity.support_transaction", locale)
    if domain == "account_query":
        return render_message("orchestrator.ambiguity.account_query", locale)
    return render_message("orchestrator.ambiguity.generic", locale)
