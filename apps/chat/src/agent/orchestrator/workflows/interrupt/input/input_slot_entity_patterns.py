"""Entity-shaped input slot shortcut patterns."""

import re

from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_text import (
    _digits_only,
    _normalize_recipient_match_text,
)
from shared.utils.network_utils import normalize_network_name

_NON_TRANSFER_INTENT_HINT_RE = re.compile(
    r"\b(airtime|data|bundle|balance|statement|support|faq|ticket|complaint)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_PREFIX_RE = re.compile(
    r"^(?:(?:it'?s|its|it is|this is)\s+)?(?:(?:to|for|send(?:\s+it)?\s+to)\s+)?(?P<recipient>.+?)$",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_BLOCK_RE = re.compile(
    r"\b(and|also|plus|then|while|cancel|stop|show|list|check|buy|help|support|faq|balance|statement|spend|spent|transaction|transactions|airtime|data|beneficiar(?:y|ies)|account(?:s)?|week|month|today|tomorrow|yesterday)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_QUESTION_RE = re.compile(r"^(what|how|why|when|where|who|which)\b", re.IGNORECASE)
_INPUT_RECIPIENT_REPLY_META_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no)$",
    re.IGNORECASE,
)
_INPUT_BANK_REPLY_BLOCK_RE = re.compile(
    r"\b(send|transfer|pay|buy|airtime|data|bundle|balance|statement|transaction|transactions|"
    r"account\s+balance|support|faq|cancel|stop|show|list|check)\b",
    re.IGNORECASE,
)
_INPUT_BANK_REPLY_PREFIX_RE = re.compile(
    r"^(?:(?:it'?s|its|it is|bank is|the bank is|use)\s+)?(?P<bank>[a-z0-9&' .-]+?)(?:\s+bank)?$",
    re.IGNORECASE,
)
_INPUT_SELF_PHONE_REPLY_RE = re.compile(
    r"^(?:for\s+)?(?:me|my\s+(?:line|number|phone)|mine|myself|this\s+line)"
    r"(?:\s+(?:please|pls|abeg|jare|na|now|o|oo))?$",
    re.IGNORECASE,
)
_INPUT_NETWORK_REPLY_BLOCK_RE = re.compile(
    r"\b(send|transfer|pay|buy|data|airtime|bundle|balance|statement|transaction|transactions|"
    r"account|support|faq|cancel|stop|show|list|check)\b",
    re.IGNORECASE,
)


def _looks_like_bank_name_reply(text: str) -> bool:
    stripped_text = text.strip()
    if not stripped_text or "?" in stripped_text:
        return False
    if _INPUT_BANK_REPLY_BLOCK_RE.search(stripped_text):
        return False
    match = _INPUT_BANK_REPLY_PREFIX_RE.fullmatch(stripped_text)
    if match is None:
        return False
    bank = (match.group("bank") or "").strip(" .,!?:;\"'()[]{}")
    if not bank or _digits_only(bank):
        return False
    tokens = bank.split()
    return 1 <= len(tokens) <= 4 and all(len(token) >= 2 for token in tokens)


def _looks_like_network_reply(text: str) -> bool:
    stripped_text = text.strip()
    if not stripped_text or "?" in stripped_text:
        return False
    if _INPUT_NETWORK_REPLY_BLOCK_RE.search(stripped_text):
        return False
    return normalize_network_name(stripped_text) is not None


def _looks_like_simple_transfer_recipient_reply(text: str) -> bool:
    stripped_text = text.strip()
    if not stripped_text:
        return False
    if _INPUT_RECIPIENT_REPLY_META_RE.fullmatch(stripped_text):
        return False
    if "?" in stripped_text or _INPUT_RECIPIENT_REPLY_QUESTION_RE.search(stripped_text):
        return False
    if _NON_TRANSFER_INTENT_HINT_RE.search(stripped_text) or _INPUT_RECIPIENT_REPLY_BLOCK_RE.search(stripped_text):
        return False

    match = _INPUT_RECIPIENT_REPLY_PREFIX_RE.fullmatch(stripped_text)
    if match is None:
        return False

    candidate = (match.group("recipient") or "").strip(" .,!?:;\"'()[]{}")
    if not candidate:
        return False

    if _digits_only(candidate):
        return False

    normalized_candidate = _normalize_recipient_match_text(candidate)
    if not normalized_candidate:
        return False

    tokens = normalized_candidate.split()
    if not tokens or len(tokens) > 4:
        return False

    return all(len(token) >= 2 for token in tokens)


__all__ = [
    "_INPUT_SELF_PHONE_REPLY_RE",
    "_looks_like_bank_name_reply",
    "_looks_like_network_reply",
    "_looks_like_simple_transfer_recipient_reply",
]
