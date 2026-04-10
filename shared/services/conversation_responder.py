"""Conversation responder service for bounded casual replies."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_openai import ChatOpenAI

from shared.i18n import LocaleManager, render_message, render_text

_LANGUAGE_LABELS = {
    "en": "English",
    "pcm": "Pidgin English",
    "yo": "Yoruba",
    "ha": "Hausa",
    "ig": "Igbo",
}
_MAX_REPLY_CHARS = 220
_MAX_REPLY_LINES = 3
_WHITESPACE_RE = re.compile(r"[ \t]+")
_MULTILINE_RE = re.compile(r"\n{3,}")
_BLOCKED_PATTERN_RE = re.compile(
    r"\b(?:investment advice|medical advice|legal advice|diagnose|prescription|"
    r"sue|lawsuit|tax advice|buy this stock|sell this stock)\b",
    re.IGNORECASE,
)
_BANKING_REFUSAL_PATTERN_RE = re.compile(
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
_JOKE_PATTERN_RE = re.compile(r"\b(?:joke|funny|laugh|another one)\b", re.IGNORECASE)


def _locale_to_language_label(raw_locale: str | None) -> str:
    locale = LocaleManager.normalize(raw_locale).value
    return _LANGUAGE_LABELS.get(locale, "English")


def _normalize_reply_text(text: str) -> str:
    cleaned_lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    collapsed = "\n".join(line for line in cleaned_lines if line)
    return _MULTILINE_RE.sub("\n\n", collapsed).strip()


def is_banking_refusal_reply(raw_text: str | None, *, locale: str) -> bool:
    if not raw_text:
        return False
    localized = _normalize_reply_text(render_text(raw_text, locale))
    if not localized:
        return False
    redirect_text = _normalize_reply_text(render_message("conversational.out_of_scope", locale))
    if localized == redirect_text:
        return True
    if localized.endswith(f"\n{redirect_text}") or localized.endswith(f" {redirect_text}"):
        return True
    return bool(_BANKING_REFUSAL_PATTERN_RE.search(localized))


class ConversationResponder:
    """Generates bounded conversational replies for casual non-banking turns."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm

    def _redirect_text(self, locale: str, *, casual_streak: int = 0) -> str:
        if casual_streak >= 2:
            return render_message("conversational.out_of_scope_firm", locale)
        if casual_streak == 1:
            return render_message("conversational.out_of_scope_followup", locale)
        return render_message("conversational.out_of_scope", locale)

    def _redirect_variants(self, locale: str) -> tuple[str, ...]:
        return (
            _normalize_reply_text(render_message("conversational.out_of_scope", locale)),
            _normalize_reply_text(render_message("conversational.out_of_scope_followup", locale)),
            _normalize_reply_text(render_message("conversational.out_of_scope_firm", locale)),
        )

    def _count_trailing_casual_replies(self, history: list[Any], *, locale: str) -> int:
        redirect_variants = self._redirect_variants(locale)
        streak = 0
        for turn in reversed(history):
            if not isinstance(turn, dict):
                break
            role = str(turn.get("role", "")).strip().lower()
            content = _normalize_reply_text(str(turn.get("content", "") or ""))
            if role == "assistant":
                if any(variant and variant in content for variant in redirect_variants):
                    streak += 1
                    continue
                break
            if role == "user":
                continue
            break
        return streak

    def _sanitize_preface(self, raw_text: str | None, *, locale: str) -> str | None:
        if not raw_text:
            return None
        localized = _normalize_reply_text(render_text(raw_text, locale))
        if not localized:
            return None
        if len(localized) > _MAX_REPLY_CHARS:
            return None
        if len([line for line in localized.splitlines() if line.strip()]) > _MAX_REPLY_LINES:
            return None
        if _BLOCKED_PATTERN_RE.search(localized):
            return None
        if is_banking_refusal_reply(localized, locale=locale):
            return None
        return localized

    async def generate_reply(
        self,
        phone_number: str,
        text: str,
        user_ctx: dict[str, Any],
        intent: str | None = None,
    ) -> str:
        """Generate a short safe reply and append a deterministic banking redirect."""
        del phone_number, intent
        profile = user_ctx.get("profile") or {}
        name = profile.get("full_name") or profile.get("first_name") if isinstance(profile, dict) else None

        locale = LocaleManager.normalize(user_ctx.get("language")).value
        language = _locale_to_language_label(locale)
        history = user_ctx.get("history") or []
        now = datetime.now(ZoneInfo("Africa/Lagos"))
        casual_streak = self._count_trailing_casual_replies(history, locale=locale)
        redirect_text = self._redirect_text(locale, casual_streak=casual_streak)
        prefers_banking_humor = bool(_JOKE_PATTERN_RE.search(text))

        history_text = ""
        if history:
            trimmed_history = history[-4:]
            history_lines = []
            for turn in trimmed_history:
                role = str(turn.get("role", "user")).strip().lower() or "user"
                content = str(turn.get("content", "")).strip()
                if content:
                    history_lines.append(f"{role}: {content}")
            if history_lines:
                history_text = "\nRecent turns:\n" + "\n".join(history_lines)

        system = (
            "You are Narya, a banking assistant on WhatsApp.\n"
            f"Reply in {language}.\n"
            "The user's message is non-banking or casual chat.\n"
            "Write ONLY a short conversational preface, not the banking redirect.\n"
            "Rules:\n"
            "- Answer briefly and harmlessly.\n"
            "- Keep it to 1 or 2 short sentences.\n"
            "- For harmless casual asks like jokes, tiny banter, or date/time, answer directly instead of refusing.\n"
            "- If the user asks for a joke or playful banter, prefer banking-, money-, balance-, savings-, "
            "or transfer-themed humor.\n"
            "- No financial, legal, medical, tax, or investment advice.\n"
            "- No promises about unsupported capabilities.\n"
            "- No broad topic drift, no markdown, no emojis.\n"
            "- If asked about the current date or time, use the runtime Lagos timestamp provided.\n"
            "- Do not say you only handle banking or that you cannot help with harmless casual chat.\n"
            "- If the ask is unsafe, too broad, or not suitable, return an empty string.\n"
        )
        if casual_streak >= 2:
            system += "- The user has stayed in casual-chat mode for several turns, so keep the reply extra short.\n"

        user_parts = [
            f"Runtime Lagos timestamp: {now.strftime('%A, %B %d, %Y %H:%M %Z')}",
            f"User message: {text.strip()}",
            f"Recent casual streak: {casual_streak}",
        ]
        if prefers_banking_humor:
            user_parts.append("Use a banking-related joke or money-themed playful line if you answer with humor.")
        if name:
            user_parts.append(f"User name: {name}")
        if history_text:
            user_parts.append(history_text)

        reply = await self.llm.ainvoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(user_parts)},
            ]
        )

        raw_content: str | None
        if isinstance(reply, str):
            raw_content = reply
        else:
            response_content: Any = getattr(reply, "content", None)
            raw_content = response_content if isinstance(response_content, str) else None

        preface = self._sanitize_preface(raw_content, locale=locale)
        if not preface:
            return redirect_text
        if preface == redirect_text:
            return redirect_text
        return f"{preface}\n{redirect_text}"
