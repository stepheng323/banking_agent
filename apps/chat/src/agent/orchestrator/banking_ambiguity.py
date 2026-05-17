"""Deterministic guardrails for malformed banking-coded user turns."""

from __future__ import annotations

import re
from typing import Literal

from apps.chat.src.agent.shared.routing_signals import looks_like_explicit_transaction_query_shape

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
        return "data"
    if _AIRTIME_SELF_DIRECTED_RE.search(normalized) or _RECHARGE_ME_RE.search(normalized):
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
    del locale
    domain = classify_banking_coded_ambiguity(text)
    if domain == "transfer":
        return "Do you want to send money? If yes, who is the recipient?"
    if domain == "airtime":
        return "Do you want to buy airtime? If yes, whose line is it for?"
    if domain == "data":
        return "Do you want to buy data? If yes, whose line is it for?"
    if domain == "support":
        return "Which transaction do you want me to check?"
    if domain == "account_query":
        return "Do you want to check your balance or look up a transaction?"
    return "Tell me the banking action you want me to help with."
