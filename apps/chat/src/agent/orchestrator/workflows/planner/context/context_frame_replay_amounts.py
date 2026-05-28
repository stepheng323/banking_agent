"""Amount parsing helpers for context-frame replay modifiers."""

import re

_REPLAY_AMOUNT_TOKEN_RE = re.compile(
    r"(?P<prefix>₦|ngn|naira)?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<suffix>[km])?\b",
    re.IGNORECASE,
)
_REPLAY_AMOUNT_OVERRIDE_RE = re.compile(
    r"\b(?:but|with|for|at|instead)\b\s+(?:with\s+)?"
    r"(?P<token>(?:₦|ngn|naira)?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*[km]?"
    r"|(?:₦|ngn|naira)?\s*\d+(?:\.\d+)?\s*[km]?)\b|"
    r"\b(?:make|change|set|update)\s+(?:it|amount|this|that)?\s*(?:to\s+)?"
    r"(?P<edit_token>(?:₦|ngn|naira)?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*[km]?"
    r"|(?:₦|ngn|naira)?\s*\d+(?:\.\d+)?\s*[km]?)\b",
    re.IGNORECASE,
)


def _parse_replay_amount_token(token: str | None) -> float | None:
    if not token:
        return None

    match = _REPLAY_AMOUNT_TOKEN_RE.search(token)
    if not match:
        return None

    try:
        value = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None

    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        value *= 1000
    elif suffix == "m":
        value *= 1_000_000

    if value <= 0:
        return None

    has_explicit_money_marker = bool(match.group("prefix") or suffix or "," in match.group("amount"))
    if not has_explicit_money_marker and value > 100_000_000:
        return None

    return value


def _replay_amount_override(text: str | None) -> float | None:
    if not text:
        return None

    match = _REPLAY_AMOUNT_OVERRIDE_RE.search(text)
    if not match:
        return None
    token = match.group("token") or match.group("edit_token")
    return _parse_replay_amount_token(token)


def _text_is_replay_amount_token(value: str) -> bool:
    return bool(_REPLAY_AMOUNT_TOKEN_RE.fullmatch(value.strip()))


__all__ = [
    "_parse_replay_amount_token",
    "_replay_amount_override",
    "_text_is_replay_amount_token",
]
