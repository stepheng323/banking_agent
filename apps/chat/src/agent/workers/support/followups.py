"""Contextual support follow-up heuristics."""

import re
from typing import Any

from apps.chat.src.agent.shared.routing_signals import looks_like_transaction_replay_modifier_request
from apps.chat.src.agent.workers.support.models import SupportIntent, TransactionReference

_RECENT_TRANSACTION_STATUS_ASSERTION_RE = re.compile(
    r"\b(?:my|the|this|that)?\s*(?:last|latest|most\s+recent|recent)\s+"
    r"(?:transaction|transfer|payment)\s+"
    r"(?:failed|fail(?:ed)?|declined|rejected|is\s+pending|is\s+processing|is\s+stuck|"
    r"was\s+pending|was\s+processing|was\s+stuck|didn['’]?t\s+go\s+through)\b",
    re.IGNORECASE,
)
_RECENT_TRANSACTION_REFERENCE_RE = re.compile(
    r"^\s*(?:my|the|this|that)?\s*(?:last|latest|most\s+recent|recent)\s+"
    r"(?:transaction|transfer|payment)\s*$"
    r"|^\s*(?:the\s+)?(?:last|latest|recent)\s+one\s*$"
    r"|^\s*(?:it|this|that|that\s+one)\s*$",
    re.IGNORECASE,
)
_DETAIL_FOLLOWUP_RE = re.compile(
    r"\b(?:details?|full\s+details?|more\s+info(?:rmation)?|show\s+(?:me\s+)?(?:the\s+)?details?)\b",
    re.IGNORECASE,
)
_STATUS_FOLLOWUP_RE = re.compile(
    r"\b(?:status|state|what\s+happened|did\s+it\s+(?:go\s+through|fail|succeed))\b",
    re.IGNORECASE,
)
_RETRY_FOLLOWUP_RE = re.compile(r"\b(?:retry|try\s+again|resend|send\s+again)\b", re.IGNORECASE)


def recent_status_priority(intent: SupportIntent) -> list[str] | None:
    if intent in {SupportIntent.FAILED_TRANSFER, SupportIntent.RETRY_TRANSFER}:
        return ["failed", "processing", "pending"]
    if intent == SupportIntent.PENDING_TRANSFER:
        return ["pending", "processing", "failed"]
    return None


def should_verify_latest_transaction_status(intent: SupportIntent, message: str) -> bool:
    if intent not in {
        SupportIntent.FAILED_TRANSFER,
        SupportIntent.PENDING_TRANSFER,
        SupportIntent.GENERAL_TX_ISSUE,
        SupportIntent.TRANSFER_STATUS,
    }:
        return False
    return bool(_RECENT_TRANSACTION_STATUS_ASSERTION_RE.search(message))


def is_recent_transaction_reference(message: str) -> bool:
    return bool(_RECENT_TRANSACTION_REFERENCE_RE.search(message or ""))


def contextual_followup_reference(
    *,
    support_ctx: Any,
    message: str,
) -> tuple[SupportIntent | None, TransactionReference | None]:
    last_ref = str(getattr(support_ctx, "last_transaction_ref", "") or "").strip()
    if last_ref:
        if _RETRY_FOLLOWUP_RE.search(message) and not looks_like_transaction_replay_modifier_request(message):
            return SupportIntent.RETRY_TRANSFER, TransactionReference(transaction_id=last_ref)
        if _DETAIL_FOLLOWUP_RE.search(message) or _STATUS_FOLLOWUP_RE.search(message):
            return SupportIntent.TRANSFER_STATUS, TransactionReference(transaction_id=last_ref)

    pending_reference = getattr(support_ctx, "pending_reference", None)
    asked_for_reference = getattr(support_ctx, "last_support_step", None) == "asked_for_reference"
    if (pending_reference is not None or asked_for_reference) and is_recent_transaction_reference(message):
        fallback_intent = getattr(support_ctx, "last_issue_intent", None) or SupportIntent.GENERAL_TX_ISSUE
        return fallback_intent, TransactionReference(use_recent=True)

    return None, None


__all__ = [
    "contextual_followup_reference",
    "is_recent_transaction_reference",
    "recent_status_priority",
    "should_verify_latest_transaction_status",
]
