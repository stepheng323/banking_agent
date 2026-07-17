"""Lightweight text hint detection for planner context modes."""

import re

from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

_TX_HINT_KEYWORDS = (
    "send",
    "transfer",
    "pay",
    "buy",
    "airtime",
    "data",
    "bundle",
    "firanse",
    "ra",
    "saya",
    "tura",
    "ziga",
    "envoye",
    "envoyer",
)
_TX_HINT_PATTERN = re.compile(
    rf"(?<!\w)(?:{'|'.join(re.escape(keyword) for keyword in _TX_HINT_KEYWORDS)})(?!\w)",
    re.IGNORECASE,
)
_PHONE_HINT_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_AMOUNT_HINT_PATTERN = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?\b")


def _has_transaction_intent_hint(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False

    if _TX_HINT_PATTERN.search(normalized):
        return True

    if _AMOUNT_HINT_PATTERN.search(normalized):
        return True

    for candidate in _PHONE_HINT_PATTERN.findall(normalized):
        if normalize_nigerian_phone(candidate):
            return True

    # Network mentions are strong transaction hints.
    for token in re.findall(r"[A-Za-z0-9]+", normalized):
        if normalize_network_name(token) or token in {"mtn", "glo", "airtel", "9mobile"}:
            return True

    return False


def _looks_like_terse_context_frame_followup(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    tokens = re.findall(r"[\w']+", normalized, re.UNICODE)
    return len(tokens) <= 4


__all__ = ["_has_transaction_intent_hint", "_looks_like_terse_context_frame_followup"]
