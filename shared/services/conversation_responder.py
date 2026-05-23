"""Conversation responder service for bounded casual replies."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_openai import ChatOpenAI

from shared.assistant_profile.voice import build_conversation_voice_block
from shared.i18n import LocaleManager, render_message, render_text
from shared.i18n.message_keys import as_message_key

_LANGUAGE_LABELS = {
    "en": "English",
    "pcm": "Pidgin English",
    "yo": "Yoruba",
    "ha": "Hausa",
    "ig": "Igbo",
}
_MAX_REPLY_CHARS = 220
_MAX_REPLY_LINES = 3
_MAX_CASUAL_REPLY_STREAK = 3
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
_JOKE_FOLLOWUP_PATTERN_RE = re.compile(
    r"\b(?:another one|one more|again|another joke|small joke|small one|more)\b",
    re.IGNORECASE,
)
_BANKING_RESULT_CONTEXT_RE = re.compile(
    r"(?:"
    r"\u20a6\s*\d|"
    r"\b(?:account\s+balances?|total\s+of|transfer\s+of|sent\s+[\u2014-]|received\s+[\u2014-]|"
    r"transaction\s+(?:was\s+)?(?:successful|pending|failed)|"
    r"(?:successful|pending|failed)\s+(?:transaction|transfer|payment)|"
    r"receipt|showing\s+\d+\s*[-\u2013]\s*\d+)\b"
    r")",
    re.IGNORECASE,
)
_BANKING_REACTION_RE = re.compile(
    r"\b(?:"
    r"(?:am\s+i|i\s+am|i'm|im|so\s+i\s+am|so\s+i'm|so\s+im|this\s+is|that\s+is|"
    r"this\s+one|that\s+one|my\s+balance|my\s+money)\b.*"
    r"\b(?:poor|broke|rich|wealthy|worth|enough|low|small|bad|good|sad|happy|"
    r"worried|scared|ashamed)|"
    r"^(?:wow|okay|ok|nice|great|good|bad|hmm|chai|omo)[.!?]*$"
    r")",
    re.IGNORECASE,
)
_BANKING_JOKE_FALLBACKS = (
    "Why did the banker bring a ladder? To reach the next interest level.",
    "Why do bankers love balance? Because it always checks out.",
    "Why was the debit card calm? It knew how to keep its balance.",
)
_CONTEXTUAL_WORKER_FOLLOWUP_INTENT = "contextual_worker_followup"
_CONTEXTUAL_ACTION_PROMISE_RE = re.compile(
    r"\b(?:i(?:'ll| will)|let me|i can)\s+"
    r"(?:retry|resend|send|transfer|buy|purchase|create|open|raise|submit|reverse|refund)\b",
    re.IGNORECASE,
)
_CONTEXTUAL_UNGROUNDED_PREFACE_RE = re.compile(
    r"\b(?:glad\s+it\s+looked|looked\s+better\s+than\s+expected|better\s+than\s+expected)\b",
    re.IGNORECASE,
)
_CONTEXTUAL_FAILURE_BELIEF_RE = re.compile(
    r"\b(?:"
    r"(?:thought|assumed|figured|was\s+thinking|was\s+worried|worried)\b"
    r".*\b(?:fail(?:ed)?|declined|unsuccessful|did(?:n't| not)\s+go)|"
    r"i\s+(?:bin\s+|been\s+)?think\s+say\b.*\b(?:fail(?:ed)?|no\s+go|not\s+go|decline)|"
    r"mo\s+(?:ro|lero|ni\s+lokan)\s+pe\b.*\b(?:o\s+)?(?:kuna|fail|ko\s+(?:se|lo|work))|"
    r"(?:na\s+(?:yi\s+)?zaton|na\s+dauka|ina\s+tunanin)\b.*"
    r"\b(?:ya\s+)?(?:fadi|kasa|fail|gaza|bai\s+yi\s+nasara)|"
    r"(?:eche(?:re)?\s+m\s+na|a\s+chere\s+m\s+na)\b.*"
    r"\b(?:fail|dara\s+ada|gaghi\s+nke\s+oma|agara\s+ghi\s+nke\s+oma)"
    r")\b",
    re.IGNORECASE,
)
_CONTEXTUAL_SUCCESS_RE = re.compile(
    r"\b(?:"
    r"(?:was|is)\s+(?:actually\s+)?successful|"
    r"successful\s+on|completed|settled|went\s+through|no\s+failure\s+occurred|"
    r"don\s+go|go\s+well|seyori|a\s+seyori|ya\s+yi\s+nasara|an\s+kammala|"
    r"gara\s+nke\s+oma|agara\s+nke\s+oma|emechara"
    r")\b",
    re.IGNORECASE,
)
_CONTEXTUAL_PENDING_RE = re.compile(
    r"\b(?:pending|processing|still\s+being\s+processed|dey\s+process|ana\s+aiwatar|ka\s+na\s+processing)\b",
    re.IGNORECASE,
)


def _recent_history_text(history: list[Any], *, limit: int = 4) -> str:
    lines: list[str] = []
    for turn in history[-limit:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "user")).strip().lower() or "user"
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _locale_to_language_label(raw_locale: str | None) -> str:
    locale = LocaleManager.normalize(raw_locale).value
    return _LANGUAGE_LABELS.get(locale, "English")


def _normalize_reply_text(text: str) -> str:
    cleaned_lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    collapsed = "\n".join(line for line in cleaned_lines if line)
    return _MULTILINE_RE.sub("\n\n", collapsed).strip()


def _fold_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return _WHITESPACE_RE.sub(" ", without_marks.casefold()).strip()


def _contextual_worker_context_blob(user_ctx: dict[str, Any] | None) -> str:
    if not isinstance(user_ctx, dict):
        return ""
    parts: list[str] = []
    contextual_summary = user_ctx.get(_CONTEXTUAL_WORKER_FOLLOWUP_INTENT)
    if isinstance(contextual_summary, str) and contextual_summary.strip():
        parts.append(contextual_summary.strip())
    history = user_ctx.get("history")
    if isinstance(history, list):
        for turn in history[-4:]:
            if not isinstance(turn, dict):
                continue
            content = str(turn.get("content") or "").strip()
            if content:
                parts.append(content)
    return "\n".join(parts)


def _contextual_worker_subject_key(context_blob: str) -> str:
    lowered = _fold_text(context_blob)
    if re.search(r"\b(?:transfer|tura\s+kudi|aika\s+kudi|gbe\s+owo|ziga\s+ego|zipu\s+ego)\b", lowered):
        return "transfer"
    if re.search(r"\bairtime\b", lowered):
        return "airtime_purchase"
    if re.search(r"\bdata\b", lowered):
        return "data_purchase"
    if re.search(r"\b(?:transaction|payment)\b", lowered):
        return "transaction"
    return "request"


def _contextual_worker_subject_label(subject_key: str, locale: str) -> str:
    return render_message(
        as_message_key(f"conversational.contextual_worker_followup.subject.{subject_key}"),
        locale,
        fallback_en=subject_key.replace("_", " "),
    )


def _contextual_worker_message(
    message_key: str,
    locale: str,
    *,
    subject_key: str | None = None,
) -> str:
    params: dict[str, object] | None = None
    if subject_key:
        params = {"subject": _contextual_worker_subject_label(subject_key, locale)}
    return render_message(as_message_key(f"conversational.contextual_worker_followup.{message_key}"), locale, params)


def _contextual_worker_grounded_reply(
    text: str | None,
    user_ctx: dict[str, Any] | None,
    locale: str,
) -> str | None:
    normalized = _fold_text(text)
    if not _CONTEXTUAL_FAILURE_BELIEF_RE.search(normalized):
        return None
    context_blob = _contextual_worker_context_blob(user_ctx)
    if not context_blob:
        return None
    folded_context = _fold_text(context_blob)
    subject_key = _contextual_worker_subject_key(context_blob)
    if _CONTEXTUAL_SUCCESS_RE.search(folded_context):
        return _contextual_worker_message("success", locale, subject_key=subject_key)
    if _CONTEXTUAL_PENDING_RE.search(folded_context):
        return _contextual_worker_message("pending", locale, subject_key=subject_key)
    return None


def contextual_worker_fallback_reply(
    text: str | None,
    user_ctx: dict[str, Any] | None = None,
    *,
    locale: str | None = None,
) -> str:
    resolved_locale = LocaleManager.normalize(locale or (user_ctx or {}).get("language")).value
    grounded_reply = _contextual_worker_grounded_reply(text, user_ctx, resolved_locale)
    if grounded_reply:
        return grounded_reply
    normalized = _fold_text(text)
    if re.search(r"\b(?:thought|assumed|figured|worried|my bad|mistake|mistaken)\b", normalized):
        return _contextual_worker_message("settled", resolved_locale)
    return _contextual_worker_message("generic", resolved_locale)


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


def is_contextual_casual_followup_turn(text: str | None, history: list[Any] | None) -> bool:
    if not text:
        return False
    if not _JOKE_FOLLOWUP_PATTERN_RE.search(text):
        return False
    history_text = _recent_history_text(history or [])
    if not history_text:
        return False
    return bool(_JOKE_PATTERN_RE.search(history_text))


def _is_banking_result_reaction(text: str | None, history: list[Any] | None) -> bool:
    folded_text = _fold_text(text)
    if not folded_text or len(folded_text.split()) > 16:
        return False
    if _JOKE_PATTERN_RE.search(folded_text) or _BLOCKED_PATTERN_RE.search(folded_text):
        return False
    if not _BANKING_REACTION_RE.search(folded_text):
        return False
    for turn in reversed(history or []):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "")).strip().lower()
        if role != "assistant":
            continue
        content = _fold_text(str(turn.get("content", "") or ""))
        if _BANKING_RESULT_CONTEXT_RE.search(content):
            return True
    return False


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

    def _recent_history_text(self, history: list[Any], *, limit: int = 4) -> str:
        return _recent_history_text(history, limit=limit)

    def _is_joke_turn(self, text: str, history: list[Any]) -> bool:
        if _JOKE_PATTERN_RE.search(text):
            return True
        if not _JOKE_FOLLOWUP_PATTERN_RE.search(text):
            return False
        history_text = self._recent_history_text(history)
        return bool(_JOKE_PATTERN_RE.search(history_text))

    def _deterministic_joke_fallback(self, *, casual_streak: int) -> str:
        return _BANKING_JOKE_FALLBACKS[min(casual_streak, len(_BANKING_JOKE_FALLBACKS) - 1)]

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
        """Generate a short safe reply with a deterministic redirect when needed."""
        del phone_number
        profile = user_ctx.get("profile") or {}
        name = profile.get("full_name") or profile.get("first_name") if isinstance(profile, dict) else None

        locale = LocaleManager.normalize(user_ctx.get("language")).value
        language = _locale_to_language_label(locale)
        history = user_ctx.get("history") or []
        is_contextual_worker_followup = intent == _CONTEXTUAL_WORKER_FOLLOWUP_INTENT
        now = datetime.now(ZoneInfo("Africa/Lagos"))
        casual_streak = self._count_trailing_casual_replies(history, locale=locale)
        redirect_text = self._redirect_text(locale, casual_streak=casual_streak)
        prefers_banking_humor = bool(_JOKE_PATTERN_RE.search(text))
        if not is_contextual_worker_followup and casual_streak >= _MAX_CASUAL_REPLY_STREAK:
            return redirect_text

        history_text = self._recent_history_text(history)
        history_block = f"\nRecent turns:\n{history_text}" if history_text else ""
        is_joke_turn = self._is_joke_turn(text, history)
        is_banking_reaction = (
            not is_contextual_worker_followup
            and casual_streak == 0
            and _is_banking_result_reaction(text, history)
        )
        if is_contextual_worker_followup:
            grounded_reply = _contextual_worker_grounded_reply(text, user_ctx, locale)
            if grounded_reply:
                return grounded_reply

        if is_contextual_worker_followup:
            system = (
                build_conversation_voice_block(locale=language, channel="WhatsApp")
                + f"Reply in {language}.\n"
                "The user's message is an acknowledgement or commentary after a banking assistant result.\n"
                "Write ONLY a short grounded acknowledgement.\n"
                "Rules:\n"
                "- Use only the recent context provided.\n"
                "- Keep it to 1 short sentence.\n"
                "- Do not ask for a transaction reference.\n"
                "- Do not start a support, query, transfer, airtime, data, account, or FAQ workflow.\n"
                "- Do not offer to retry, send money, buy anything, create tickets, refund, or reverse anything.\n"
                "- No generic banking redirect.\n"
                "- No markdown, no emojis.\n"
                "- If no specific grounded acknowledgement is possible, return an empty string.\n"
            )
        else:
            system = (
                build_conversation_voice_block(locale=language, channel="WhatsApp")
                + f"Reply in {language}.\n"
                "The user's message is non-banking or casual chat.\n"
                "Write ONLY a short conversational preface, not the banking redirect.\n"
                "Rules:\n"
                "- Answer briefly and harmlessly.\n"
                "- Keep it to 1 or 2 short sentences.\n"
                "- For harmless casual asks like jokes, tiny banter, or date/time, answer directly "
                "instead of refusing.\n"
                "- If the user asks for a joke or playful banter, prefer banking-, money-, balance-, savings-, "
                "or transfer-themed humor.\n"
                "- No financial, legal, medical, tax, or investment advice.\n"
                "- No promises about unsupported capabilities.\n"
                "- No broad topic drift, no markdown, no emojis.\n"
                "- If asked about the current date or time, use the runtime Lagos timestamp provided.\n"
                "- Do not say you only handle banking or that you cannot help with harmless casual chat.\n"
                "- If the ask is unsafe, too broad, or not suitable, return an empty string.\n"
            )
            if is_banking_reaction:
                system += (
                    "- The user is reacting to recent banking information. Give only the short empathetic "
                    "reply; no generic banking redirect.\n"
                )
        if not is_contextual_worker_followup and casual_streak >= 2:
            system += "- The user has stayed in casual-chat mode for several turns, so keep the reply extra short.\n"

        user_parts = [
            f"Runtime Lagos timestamp: {now.strftime('%A, %B %d, %Y %H:%M %Z')}",
            f"User message: {text.strip()}",
            f"Recent casual streak: {casual_streak}",
        ]
        contextual_summary = user_ctx.get(_CONTEXTUAL_WORKER_FOLLOWUP_INTENT)
        if is_contextual_worker_followup and contextual_summary:
            user_parts.append(f"Recent banking context: {contextual_summary}")
        if not is_contextual_worker_followup and (prefers_banking_humor or is_joke_turn):
            user_parts.append("Use a banking-related joke or money-themed playful line if you answer with humor.")
        if name:
            user_parts.append(f"User name: {name}")
        if history_block:
            user_parts.append(history_block)

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
            if is_contextual_worker_followup:
                return contextual_worker_fallback_reply(text, user_ctx, locale=locale)
            if is_joke_turn:
                return f"{self._deterministic_joke_fallback(casual_streak=casual_streak)}\n{redirect_text}"
            return redirect_text
        if is_contextual_worker_followup:
            if _CONTEXTUAL_ACTION_PROMISE_RE.search(preface) or _CONTEXTUAL_UNGROUNDED_PREFACE_RE.search(preface):
                return contextual_worker_fallback_reply(text, user_ctx, locale=locale)
            return preface
        if is_banking_reaction:
            return preface
        if preface == redirect_text:
            return redirect_text
        return f"{preface}\n{redirect_text}"
