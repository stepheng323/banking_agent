"""Helpers for deterministic conversational response styling."""

from __future__ import annotations

import re

from shared.i18n import render_message, render_text

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_TRIM_RE = re.compile(r"[.!?]+$")


def _normalize_space(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalized_compare(value: str) -> str:
    return _PUNCT_TRIM_RE.sub("", _normalize_space(value).lower())


def _extract_empathy_sentence(raw_text: str, locale: str) -> str:
    localized = render_text(raw_text, locale)
    first_line = localized.splitlines()[0] if localized else ""
    candidate = _normalize_space(first_line)
    if not candidate:
        return ""
    first_sentence = _SENTENCE_SPLIT_RE.split(candidate, maxsplit=1)[0].strip()
    if not first_sentence:
        return ""
    if first_sentence[-1] not in ".!?":
        first_sentence = f"{first_sentence}."
    return first_sentence


def format_out_of_scope_reply(locale: str, empathy_source: str | None = None) -> str:
    """Return deterministic out-of-scope redirect with optional empathy preface."""
    redirect_text = render_message("conversational.out_of_scope", locale)
    if not empathy_source:
        return redirect_text

    empathy_sentence = _extract_empathy_sentence(empathy_source, locale)
    if not empathy_sentence:
        return redirect_text

    if _normalized_compare(empathy_sentence) == _normalized_compare(redirect_text):
        return redirect_text
    redirect_lead_sentence = _extract_empathy_sentence(redirect_text, locale)
    if redirect_lead_sentence and _normalized_compare(empathy_sentence) == _normalized_compare(redirect_lead_sentence):
        return redirect_text
    return f"{empathy_sentence}\n{redirect_text}"


__all__ = ["format_out_of_scope_reply"]
