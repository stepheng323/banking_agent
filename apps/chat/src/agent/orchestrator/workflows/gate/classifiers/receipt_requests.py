import re
from typing import Any

_RECEIPT_REQUEST_RE = re.compile(
    r"\b(?:receipt|proof\s+of\s+payment|payment\s+receipt|show\s+receipt|send\s+receipt)\b",
    re.IGNORECASE,
)
_RECEIPT_SELECTOR_FOLLOWUP_RE = re.compile(
    r"\b(?:both|all|every|except|excluding|only|just|other(?:\s+one)?|remaining|rest|"
    r"first|second|third|fourth|fifth|last)\b|"
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\b|"
    r"\bone\s+for\b",
    re.IGNORECASE,
)


def _has_receipt_thread_candidates(receipt_thread_state: Any) -> bool:
    if receipt_thread_state is None:
        return False
    candidates = getattr(receipt_thread_state, "candidates", None)
    return isinstance(candidates, list) and bool(candidates)


def _looks_like_receipt_request(message_text: str) -> bool:
    return bool(_RECEIPT_REQUEST_RE.search(message_text or ""))


def _looks_like_receipt_selector_followup(message_text: str) -> bool:
    return bool(_RECEIPT_SELECTOR_FOLLOWUP_RE.search(message_text or ""))
