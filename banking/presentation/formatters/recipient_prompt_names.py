"""Recipient display-name safety for user-facing prompts."""

from __future__ import annotations

import re

from banking.policy.guardrails.loader import get_cached_guardrails
from banking.presentation.i18n.renderer import render_message

_UNSAFE_RECIPIENT_TOKENS = {
    "send",
    "transfer",
    "pay",
    "recipient",
    "her",
    "him",
    "them",
    "that",
    "it",
    "this",
    "previous",
    "to",
    "for",
    "money",
    "cash",
    "funds",
    "s",
}
_FIRST_PERSON_POSSESSIVE_TOKENS = {"my", "our"}


def normalized_prompt_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    lowered = value.strip().lower()
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", lowered)
    return [token for token in re.sub(r"[^a-z0-9]+", " ", lowered).split() if token]


def _relationship_display_from_tokens(tokens: list[str]) -> str | None:
    if not tokens:
        return None

    aliases = set()
    for alias in get_cached_guardrails().transfer.relational_aliases:
        alias_tokens = normalized_prompt_tokens(alias)
        if alias_tokens:
            aliases.add(" ".join(alias_tokens))
    if not aliases:
        return None

    whole_phrase = " ".join(tokens)
    if whole_phrase in aliases:
        return whole_phrase
    if tokens[0] in aliases:
        return tokens[0]
    return None


def sanitize_recipient_display_name(recipient_name: str | None, locale: str = "en") -> str:
    """Return a safe recipient label for user-facing prompts."""
    fallback = render_message("response.common.recipient_fallback", locale)
    if not recipient_name:
        return fallback

    tokens = normalized_prompt_tokens(recipient_name)
    if not tokens:
        return fallback
    if all(token in _UNSAFE_RECIPIENT_TOKENS for token in tokens):
        return fallback
    if tokens[0] in _FIRST_PERSON_POSSESSIVE_TOKENS:
        relationship_display = _relationship_display_from_tokens(tokens[1:])
        if relationship_display and locale == "en":
            return f"your {relationship_display}"
        return "the recipient" if locale == "en" else fallback
    relationship_display = _relationship_display_from_tokens(tokens)
    if relationship_display and locale == "en":
        return f"your {relationship_display}"
    return recipient_name


def possessive_display_name(display_name: str, locale: str = "en") -> str:
    if locale != "en":
        return display_name
    name = display_name.strip()
    if not name:
        return display_name
    if name.lower().startswith("your "):
        return f"{name}'s"
    if name[-1].lower() == "s":
        return f"{name}'"
    return f"{name}'s"
