"""Cheap classifiers for obvious non-action conversational turns."""

import re

from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.deterministic import (
    classify_deterministic_meta_response,
)

_BANKING_OR_ACTION_TERM_RE = re.compile(
    r"\b(?:"
    r"send|transfer|pay|buy|purchase|recharge|top\s*up|airtime|data|bundle|"
    r"balance|balances|transaction|transactions|history|statement|receipt|"
    r"beneficiar\w*|account|accounts|schedule|scheduled|recurring|"
    r"status|refund|reverse|reversal|complaint|support|ticket|"
    r"amount|money|cash|naira|ngn"
    r")\b",
    re.IGNORECASE,
)
_CASUAL_CHAT_REQUEST_RE = re.compile(
    r"\b(?:"
    r"jokes?|make\s+me\s+laugh|funny|banter|gist|"
    r"tell\s+me\s+(?:one\s+)?joke|"
    r"(?:fit|can|could)\s+tell\s+me\s+(?:one\s+)?joke|"
    r"fun\s+facts?|interesting\s+facts?|weird\s+(?:facts?|but\s+true)|"
    r"(?:strange|surprising)\s+(?:facts?|but\s+true)|"
    r"tell\s+me\s+(?:a\s+)?fun\s+fact|"
    r"(?:fit|can|could)?\s*tell\s+me\s+something\s+(?:so\s+)?"
    r"(?:weird|strange|interesting|surprising)(?:\s+but\s+true)?"
    r")\b",
    re.IGNORECASE,
)
_CASUAL_REACTION_RE = re.compile(
    r"^\s*(?:"
    r"(?:ha){2,}|(?:he){2,}|lol+|lmao|rofl|"
    r"you\s+(?:wicked|funny|mad|crazy|dey\s+try|too\s+much)|"
    r"omo|chai|e\s+choke|na\s+wa|wahala"
    r")\b",
    re.IGNORECASE,
)


def looks_like_obvious_casual_or_meta_turn(text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized or len(normalized) > 180:
        return False
    meta_response = classify_deterministic_meta_response(normalized)
    if meta_response is not None and str(meta_response.response_key).startswith("conversational."):
        return True
    if _BANKING_OR_ACTION_TERM_RE.search(normalized):
        return False
    return bool(_CASUAL_CHAT_REQUEST_RE.search(normalized) or _CASUAL_REACTION_RE.search(normalized))


__all__ = ["looks_like_obvious_casual_or_meta_turn"]
