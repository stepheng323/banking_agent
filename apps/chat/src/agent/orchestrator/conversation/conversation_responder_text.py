"""Text heuristics for bounded conversational replies."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import (
    render_message,
    render_text,
)

LANGUAGE_LABELS = {
    "en": "English",
    "pcm": "Pidgin English",
    "yo": "Yoruba",
    "ha": "Hausa",
    "ig": "Igbo",
}
MAX_REPLY_CHARS = 220
MAX_REPLY_LINES = 3
MAX_CASUAL_REPLY_STREAK = 3
WHITESPACE_RE = re.compile(r"[ \t]+")
MULTILINE_RE = re.compile(r"\n{3,}")
BLOCKED_PATTERN_RE = re.compile(
    r"\b(?:investment advice|medical advice|legal advice|diagnose|prescription|"
    r"sue|lawsuit|tax advice|buy this stock|sell this stock)\b",
    re.IGNORECASE,
)
BANKING_REFUSAL_PATTERN_RE = re.compile(
    r"\b(?:"
    r"i can(?:not|'t)\s+(?:help|provide|do)\b|"
    r"sorry[, ]+\s*i can(?:not|'t)\b|"
    r"i(?:'m| am)\s+here\s+to\s+help\s+with\s+(?:your\s+)?banking\b|"
    r"i\s+stay\s+on\s+banking\b|"
    r"tell\s+me\s+what\s+you\s+want\s+to\s+do\s+with\s+your\s+money\b|"
    r"banking\s+(?:tasks?|needs?)\s+only\b"
    r")",
    re.IGNORECASE,
)
SOCIAL_META_REFUSAL_PATTERN_RE = re.compile(
    r"\b(?:"
    r"i can(?:not|'t)\s+(?:help|provide|do)\b|"
    r"sorry[, ]+\s*i can(?:not|'t)\b|"
    r"i\s+stay\s+on\s+banking\b|"
    r"(?:banking|money)\s+(?:tasks?|needs?)\s+only\b|"
    r"(?:only|just)\s+(?:handle|do|support)\s+(?:banking|money)\b"
    r")",
    re.IGNORECASE,
)
JOKE_PATTERN_RE = re.compile(
    r"\b(?:"
    r"tell\s+me\s+(?:(?:a|one|small)\s+)?joke|"
    r"tell\s+me\s+(?:\w+\s+){0,3}joke|"
    r"give\s+me\s+(?:a\s+)?joke|"
    r"say\s+(?:a\s+)?joke|"
    r"make\s+me\s+laugh|"
    r"something\s+funny|"
    r"funny\s+(?:joke|line)"
    r")\b",
    re.IGNORECASE,
)
CASUAL_FACT_PATTERN_RE = re.compile(
    r"\b(?:"
    r"fun\s+facts?|interesting\s+facts?|weird\s+(?:facts?|but\s+true)|"
    r"(?:strange|surprising)\s+(?:facts?|but\s+true)|"
    r"tell\s+me\s+(?:a\s+)?fun\s+fact|"
    r"tell\s+me\s+something\s+(?:so\s+)?(?:weird|strange|interesting|surprising)(?:\s+but\s+true)?"
    r")\b",
    re.IGNORECASE,
)
CASUAL_FOLLOWUP_PATTERN_RE = re.compile(
    r"\b(?:tell\s+me\s+more|another one|one more|again|continue|another joke|small joke|small one|more)\b",
    re.IGNORECASE,
)
CONTEXT_FRAME_DISPLAY_FOLLOWUP_RE = re.compile(
    r"\b(?:"
    r"show|view|see|display|open|list|details?|transaction|transactions|transfer|"
    r"airtime|data|receipt|receipts|account|accounts|balance|balances|history|statement"
    r")\b",
    re.IGNORECASE,
)
BANKING_RESULT_CONTEXT_RE = re.compile(
    r"(?:"
    r"\u20a6\s*\d|"
    r"\b(?:account\s+balances?|total\s+of|transfer\s+of|sent\s+[\u2014-]|received\s+[\u2014-]|"
    r"transaction\s+(?:was\s+)?(?:successful|pending|failed)|"
    r"(?:successful|pending|failed)\s+(?:transaction|transfer|payment)|"
    r"receipt|showing\s+\d+\s*[-\u2013]\s*\d+)\b"
    r")",
    re.IGNORECASE,
)
BANKING_REACTION_RE = re.compile(
    r"\b(?:"
    r"(?:am\s+i|i\s+am|i'm|im|so\s+i\s+am|so\s+i'm|so\s+im|this\s+is|that\s+is|"
    r"this\s+one|that\s+one|my\s+balance|my\s+money)\b.*"
    r"\b(?:poor|broke|rich|wealthy|worth|enough|low|small|bad|good|sad|happy|"
    r"worried|scared|ashamed)|"
    r"^(?:wow|okay|ok|nice|great|good|bad|hmm|chai|omo)[.!?]*$"
    r")",
    re.IGNORECASE,
)
def recent_history_text(history: list[Any], *, limit: int = 4) -> str:
    lines: list[str] = []
    for turn in history[-limit:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "user")).strip().lower() or "user"
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def locale_to_language_label(raw_locale: str | None) -> str:
    locale = LocaleManager.normalize(raw_locale).value
    return LANGUAGE_LABELS.get(locale, "English")


def normalize_reply_text(text: str) -> str:
    cleaned_lines = [WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    collapsed = "\n".join(line for line in cleaned_lines if line)
    return MULTILINE_RE.sub("\n\n", collapsed).strip()


def fold_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return WHITESPACE_RE.sub(" ", without_marks.casefold()).strip()


def is_banking_refusal_reply(
    raw_text: str | None,
    *,
    locale: str,
    allow_positive_banking_anchor: bool = False,
) -> bool:
    if not raw_text:
        return False
    localized = normalize_reply_text(render_text(raw_text, locale))
    if not localized:
        return False
    redirect_text = normalize_reply_text(render_message("conversational.out_of_scope", locale))
    if localized == redirect_text:
        return True
    if localized.endswith(f"\n{redirect_text}") or localized.endswith(f" {redirect_text}"):
        return True
    if allow_positive_banking_anchor:
        return bool(SOCIAL_META_REFUSAL_PATTERN_RE.search(localized))
    return bool(BANKING_REFUSAL_PATTERN_RE.search(localized))


def redirect_text(locale: str, *, casual_streak: int = 0) -> str:
    if casual_streak >= 2:
        return render_message("conversational.out_of_scope_firm", locale)
    if casual_streak == 1:
        return render_message("conversational.out_of_scope_followup", locale)
    return render_message("conversational.out_of_scope", locale)


def redirect_variants(locale: str) -> tuple[str, ...]:
    return (
        normalize_reply_text(render_message("conversational.out_of_scope", locale)),
        normalize_reply_text(render_message("conversational.out_of_scope_followup", locale)),
        normalize_reply_text(render_message("conversational.out_of_scope_firm", locale)),
    )


def count_trailing_casual_replies(history: list[Any], *, locale: str) -> int:
    variants = redirect_variants(locale)
    streak = 0
    for turn in reversed(history):
        if not isinstance(turn, dict):
            break
        role = str(turn.get("role", "")).strip().lower()
        content = normalize_reply_text(str(turn.get("content", "") or ""))
        if role == "assistant":
            if any(variant and variant in content for variant in variants):
                streak += 1
                continue
            break
        if role == "user":
            continue
        break
    return streak


def sanitize_preface(
    raw_text: str | None,
    *,
    locale: str,
    allow_positive_banking_anchor: bool = False,
) -> str | None:
    if not raw_text:
        return None
    localized = normalize_reply_text(render_text(raw_text, locale))
    if not localized:
        return None
    if len(localized) > MAX_REPLY_CHARS:
        return None
    if len([line for line in localized.splitlines() if line.strip()]) > MAX_REPLY_LINES:
        return None
    if BLOCKED_PATTERN_RE.search(localized):
        return None
    if is_banking_refusal_reply(
        localized,
        locale=locale,
        allow_positive_banking_anchor=allow_positive_banking_anchor,
    ):
        return None
    return localized


def is_contextual_casual_followup_turn(text: str | None, history: list[Any] | None) -> bool:
    if not text:
        return False
    if CONTEXT_FRAME_DISPLAY_FOLLOWUP_RE.search(text):
        return False
    if not CASUAL_FOLLOWUP_PATTERN_RE.search(text):
        return False
    history_text = recent_history_text(history or [])
    if not history_text:
        return False
    return bool(JOKE_PATTERN_RE.search(history_text) or CASUAL_FACT_PATTERN_RE.search(history_text))


def is_banking_result_reaction(text: str | None, history: list[Any] | None) -> bool:
    folded_text = fold_text(text)
    if not folded_text or len(folded_text.split()) > 16:
        return False
    if JOKE_PATTERN_RE.search(folded_text) or BLOCKED_PATTERN_RE.search(folded_text):
        return False
    if not BANKING_REACTION_RE.search(folded_text):
        return False
    for turn in reversed(history or []):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "")).strip().lower()
        if role != "assistant":
            continue
        content = fold_text(str(turn.get("content", "") or ""))
        if BANKING_RESULT_CONTEXT_RE.search(content):
            return True
    return False


def is_joke_turn(text: str, history: list[Any]) -> bool:
    if JOKE_PATTERN_RE.search(text):
        return True
    if CONTEXT_FRAME_DISPLAY_FOLLOWUP_RE.search(text):
        return False
    if not CASUAL_FOLLOWUP_PATTERN_RE.search(text):
        return False
    history_text = recent_history_text(history)
    return bool(JOKE_PATTERN_RE.search(history_text))


__all__ = [
    "BLOCKED_PATTERN_RE",
    "CASUAL_FACT_PATTERN_RE",
    "CASUAL_FOLLOWUP_PATTERN_RE",
    "JOKE_PATTERN_RE",
    "MAX_CASUAL_REPLY_STREAK",
    "MAX_REPLY_CHARS",
    "MAX_REPLY_LINES",
    "count_trailing_casual_replies",
    "fold_text",
    "is_banking_refusal_reply",
    "is_banking_result_reaction",
    "is_contextual_casual_followup_turn",
    "is_joke_turn",
    "locale_to_language_label",
    "normalize_reply_text",
    "recent_history_text",
    "redirect_text",
    "redirect_variants",
    "sanitize_preface",
]
