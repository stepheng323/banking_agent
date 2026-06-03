"""Shared intent signals used as routing hints and negative guards."""

from __future__ import annotations

import re

_SUPPORT_PROBLEM_QUERY_ALLOW_RE = re.compile(
    r"^\s*(?:show|list|view|get|check|display|see|find|search)\b.*\b"
    r"(?:transactions?|transaction\s+history|history|statement|debits?|credits?|payments?)\b"
    r"|^\s*(?:which|what)\b.*\b(?:transactions?|transfers?|payments?)\b"
    r"|^\s*how\s+many\b.*\b(?:transactions?|transfers?|payments?)\b"
    r"|\bstatus\s+of\s+(?:my\s+)?(?:last|latest|most\s+recent|this|that)?\s*"
    r"(?:transaction|transfer|payment)\b"
    r"|\b(?:my\s+)?(?:last|latest|most\s+recent)\s+(?:transaction|transfer|payment)\s+status\b",
    re.IGNORECASE,
)
_SUPPORT_PROBLEM_PATTERNS = (
    re.compile(
        r"\b(?:my|the|this|that|last|latest|recent)\s+(?:transaction|transfer|payment)\s+"
        r"(?:failed|fail(?:ed)?|pending|stuck|processing|declined)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:transaction|transfer|payment)\s+"
        r"(?:failed|fail(?:ed)?|pending|stuck|processing|declined|didn['’]?t\s+go\s+through)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i\s+(?:was\s+)?debited|money\s+(?:left|deducted)|debit(?:ed)?|deduct(?:ed)?)\b.*"
        r"\b(?:didn['’]?t|did\s+not|not|never)\s+(?:receive|reflect|arrive|go\s+through|get\s+there)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:recipient|beneficiary|they|he|she)\s+"
        r"(?:didn['’]?t|did\s+not|hasn['’]?t|has\s+not|never)\s+"
        r"(?:receive|get|got|see)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:retry|try\s+again|resend)\b.*\b(?:failed|transaction|transfer|payment)\b|"
        r"\b(?:failed|transaction|transfer|payment)\b.*\b(?:retry|try\s+again|resend)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:refund|reversal|reverse|wrong\s+debit|chargeback|"
        r"revert\s+(?:it|the\s+money|my\s+money))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:fraud|fraudulent|unauthori[sz]ed|someone\s+used\s+my\s+account|scam)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ticket|complaint|case)\b.*\b(?:status|update|happened|progress)\b|"
        r"\bwhat\s+happened\s+to\s+my\s+(?:complaint|ticket|case)\b",
        re.IGNORECASE,
    ),
)
_REPLAY_CUE_RE = re.compile(
    r"\b(?:again|redo|repeat|replay|rerun|retry|try\s+again|resend|send\s+again|same\s+again)\b",
    re.IGNORECASE,
)
_REPLAY_MODIFIER_RE = re.compile(
    r"\b(?:but|instead|change|switch|use|using|with|from|source|account|acct|narration|reason|note|memo|for)\b"
    r"|(?:₦|\bngn\b|\b\d[\d,]*(?:\.\d+)?\s*(?:k|naira|ngn)?\b)",
    re.IGNORECASE,
)


def looks_like_explicit_transaction_query_shape(raw_query: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", (raw_query or "").strip())
    if not normalized:
        return False
    return bool(_SUPPORT_PROBLEM_QUERY_ALLOW_RE.search(normalized))


def looks_like_support_problem_statement(raw_query: str | None) -> bool:
    """Return True for transaction/ticket problem statements that query should not own.

    This signal is only a routing hint or negative guard. Explicit query shapes
    such as "show failed transactions" and "status of my last transaction" stay
    query-owned.
    """
    normalized = re.sub(r"\s+", " ", (raw_query or "").strip())
    if not normalized:
        return False
    if looks_like_explicit_transaction_query_shape(normalized):
        return False
    return any(pattern.search(normalized) for pattern in _SUPPORT_PROBLEM_PATTERNS)


def looks_like_transaction_replay_modifier_request(raw_query: str | None) -> bool:
    """Return True for replay requests that are editing a prior money movement.

    Examples: "again with 10k", "resend from gtb", "send again for rent".
    These should be owned by transaction replay/context routing, not support retry,
    unless the user also describes a support problem such as failed/pending/debited.
    """
    normalized = re.sub(r"\s+", " ", (raw_query or "").strip())
    if not normalized:
        return False
    if looks_like_support_problem_statement(normalized):
        return False
    return bool(_REPLAY_CUE_RE.search(normalized) and _REPLAY_MODIFIER_RE.search(normalized))
