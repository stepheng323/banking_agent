"""Internal response-class helpers for direct-context guardrails."""

from __future__ import annotations

import re
from typing import Any, Literal

ResponseClass = Literal[
    "FACT_BOOL",
    "FACT_COUNT",
    "FACT_STATUS",
    "FACT_RECAP",
    "SURFACE_DETAIL",
    "SURFACE_LIST",
    "SURFACE_PAGINATED",
    "SURFACE_ACTIONABLE",
]

_NORMALIZE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_QUERY_DETAIL_PATTERNS = (
    re.compile(
        r"\b(show|what(?:'s| is)|give|tell)\s+(?:me\s+)?(?:my\s+)?(?:last|latest|most\s+recent)\s+"
        r"(?:transaction|transfer|payment|debit|credit)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:last|latest|most\s+recent)\s+(?:transaction|transfer|payment|debit|credit)\b",
        re.IGNORECASE,
    ),
)
_ACCOUNT_LIST_PATTERNS = (
    re.compile(r"\b(show|list)\s+(?:me\s+)?(?:my\s+)?linked\s+accounts\b", re.IGNORECASE),
    re.compile(r"\b(show|list)\s+(?:me\s+)?(?:my\s+)?accounts\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:linked\s+)?accounts\s+do\s+i\s+have\b", re.IGNORECASE),
)
_BENEFICIARY_LIST_PATTERNS = (
    re.compile(r"\b(show|list)\s+(?:me\s+)?(?:my\s+)?beneficiar(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\b(show|list)\s+(?:me\s+)?(?:my\s+)?saved\s+beneficiar(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+beneficiar(?:y|ies)\s+do\s+i\s+have\b", re.IGNORECASE),
)
_QUERY_PAGINATION_EXACT = {
    "more",
    "next",
    "show more",
    "next page",
    "show them",
    "which ones",
    "details",
    "show details",
}
_QUERY_ACTIONABLE_EXACT = {
    "receipt",
    "issue",
    "report issue",
}
_COUNT_PATTERNS = (
    re.compile(r"\bhow\s+many\s+(?:linked\s+)?accounts\b", re.IGNORECASE),
    re.compile(r"\bnumber\s+of\s+(?:my\s+)?linked\s+accounts\b", re.IGNORECASE),
    re.compile(r"\bhow\s+many\s+beneficiar(?:y|ies)\b", re.IGNORECASE),
)


def _normalize_message(message_text: str) -> str:
    return _NORMALIZE_RE.sub(" ", message_text.strip().lower())


def _compact(text: str) -> str:
    return _NON_ALNUM_RE.sub("", text.lower())


def classify_read_only_response_class(
    message_text: str,
    *,
    loaded_context: dict[str, Any] | None = None,
    query_session_snapshot: dict[str, Any] | None = None,
) -> ResponseClass | None:
    """Classify read-only turns into fact vs structured-surface response classes."""
    del loaded_context
    normalized = _normalize_message(message_text)
    if not normalized:
        return None

    if any(pattern.search(normalized) for pattern in _QUERY_DETAIL_PATTERNS):
        return "SURFACE_DETAIL"
    if any(pattern.search(normalized) for pattern in _ACCOUNT_LIST_PATTERNS):
        return "SURFACE_LIST"
    if any(pattern.search(normalized) for pattern in _BENEFICIARY_LIST_PATTERNS):
        return "SURFACE_LIST"

    if normalized in _QUERY_ACTIONABLE_EXACT and isinstance(query_session_snapshot, dict):
        if query_session_snapshot.get("session_active"):
            return "SURFACE_ACTIONABLE"
    if normalized in _QUERY_PAGINATION_EXACT and isinstance(query_session_snapshot, dict):
        if query_session_snapshot.get("session_active"):
            return "SURFACE_PAGINATED"

    if any(pattern.search(normalized) for pattern in _COUNT_PATTERNS):
        return "FACT_COUNT"

    if "where did we stop" in normalized or "what are we doing again" in normalized:
        return "FACT_RECAP"
    if "what do you need from me" in normalized or "what is remaining" in normalized or "which step" in normalized:
        return "FACT_RECAP"

    compact = _compact(normalized)
    if "ready" in normalized or compact.startswith("caniuse") or "default now" in normalized:
        return "FACT_STATUS"
    if "still have" in normalized and "saved" in normalized:
        return "FACT_BOOL"

    return None


def is_surface_response_class(response_class: ResponseClass | None) -> bool:
    return response_class in {
        "SURFACE_DETAIL",
        "SURFACE_LIST",
        "SURFACE_PAGINATED",
        "SURFACE_ACTIONABLE",
    }


__all__ = ["ResponseClass", "classify_read_only_response_class", "is_surface_response_class"]
