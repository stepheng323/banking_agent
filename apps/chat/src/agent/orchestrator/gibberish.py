"""Deterministic gibberish/spam detection for the direct gate path."""

from __future__ import annotations

import re

from shared.i18n import render_message

_NON_WORD_ONLY_RE = re.compile(r"^[^\w]+$", re.UNICODE)
_REPEATED_NON_ALNUM_RE = re.compile(r"^([^A-Za-z0-9\s])\1{4,}$")
_REPEATED_TOKEN_RE = re.compile(r"^(\S+)(?:\s+\1){2,}$", re.IGNORECASE)
_ASCII_LETTERS_AND_SPACE_RE = re.compile(r"^[a-z\s]+$")
_KEYBOARD_MASH_HINTS = ("asdf", "qwer", "zxcv", "hjkl", "werty", "cvbn")


def looks_like_gibberish(text: str | None) -> bool:
    if not text:
        return False

    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if len(normalized) < 3:
        return False

    compact = normalized.replace(" ", "")
    if not compact:
        return False

    if _REPEATED_NON_ALNUM_RE.fullmatch(compact):
        return True

    if _REPEATED_TOKEN_RE.fullmatch(normalized):
        return True

    if _NON_WORD_ONLY_RE.fullmatch(compact):
        return True

    if _ASCII_LETTERS_AND_SPACE_RE.fullmatch(normalized):
        collapsed_letters = compact
        if len(collapsed_letters) >= 8 and any(hint in collapsed_letters for hint in _KEYBOARD_MASH_HINTS):
            return True

    return False


def render_gibberish_prompt(locale: str) -> str:
    return render_message("common.gibberish_prompt", locale)


__all__ = ["looks_like_gibberish", "render_gibberish_prompt"]
