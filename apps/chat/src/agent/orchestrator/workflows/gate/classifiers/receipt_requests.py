import re
from typing import Any

from banking.support.reference_selection import is_strict_receipt_selector_message

_RECEIPT_REQUEST_RE = re.compile(
    r"\b(?:receipt|proof\s+of\s+payment|payment\s+receipt|show\s+receipt|send\s+receipt)\b",
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
    return is_strict_receipt_selector_message(message_text or "")
