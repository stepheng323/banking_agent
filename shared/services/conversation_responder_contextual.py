"""Contextual follow-up fallbacks for conversational replies."""

from __future__ import annotations

import re
from typing import Any

from shared.i18n.locale import LocaleManager
from shared.i18n.message_keys import as_message_key
from shared.i18n.renderer import render_message
from shared.services.conversation_grounding import build_conversation_grounding
from shared.services.conversation_responder_text import fold_text

CONTEXTUAL_WORKER_FOLLOWUP_INTENT = "contextual_worker_followup"
CONTEXTUAL_META_FOLLOWUP_INTENT = "contextual_meta_followup"
CONTEXTUAL_ACTION_PROMISE_RE = re.compile(
    r"\b(?:i(?:'ll| will)|let me|i can)\s+"
    r"(?:retry|resend|send|transfer|buy|purchase|create|open|raise|submit|reverse|refund)\b",
    re.IGNORECASE,
)
CONTEXTUAL_UNGROUNDED_PREFACE_RE = re.compile(
    r"\b(?:glad\s+it\s+looked|looked\s+better\s+than\s+expected|better\s+than\s+expected)\b",
    re.IGNORECASE,
)
CONTEXTUAL_FAILURE_BELIEF_RE = re.compile(
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
CONTEXTUAL_SUCCESS_RE = re.compile(
    r"\b(?:"
    r"(?:was|is)\s+(?:actually\s+)?successful|"
    r"successful\s+on|completed|settled|went\s+through|no\s+failure\s+occurred|"
    r"don\s+go|go\s+well|seyori|a\s+seyori|ya\s+yi\s+nasara|an\s+kammala|"
    r"gara\s+nke\s+oma|agara\s+nke\s+oma|emechara"
    r")\b",
    re.IGNORECASE,
)
CONTEXTUAL_PENDING_RE = re.compile(
    r"\b(?:pending|processing|still\s+being\s+processed|dey\s+process|ana\s+aiwatar|ka\s+na\s+processing)\b",
    re.IGNORECASE,
)


def _contextual_worker_context_blob(user_ctx: dict[str, Any] | None) -> str:
    if not isinstance(user_ctx, dict):
        return ""
    parts: list[str] = []
    contextual_summary = user_ctx.get(CONTEXTUAL_WORKER_FOLLOWUP_INTENT)
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
    lowered = fold_text(context_blob)
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


def contextual_worker_grounded_reply(
    text: str | None,
    user_ctx: dict[str, Any] | None,
    locale: str,
) -> str | None:
    normalized = fold_text(text)
    if not CONTEXTUAL_FAILURE_BELIEF_RE.search(normalized):
        return None
    context_blob = _contextual_worker_context_blob(user_ctx)
    if not context_blob:
        return None
    folded_context = fold_text(context_blob)
    subject_key = _contextual_worker_subject_key(context_blob)
    if CONTEXTUAL_SUCCESS_RE.search(folded_context):
        return _contextual_worker_message("success", locale, subject_key=subject_key)
    if CONTEXTUAL_PENDING_RE.search(folded_context):
        return _contextual_worker_message("pending", locale, subject_key=subject_key)
    return None


def contextual_worker_fallback_reply(
    text: str | None,
    user_ctx: dict[str, Any] | None = None,
    *,
    locale: str | None = None,
) -> str:
    resolved_locale = LocaleManager.normalize(locale or (user_ctx or {}).get("language")).value
    grounded_reply = contextual_worker_grounded_reply(text, user_ctx, resolved_locale)
    if grounded_reply:
        return grounded_reply
    normalized = fold_text(text)
    if re.search(r"\b(?:thought|assumed|figured|worried|my bad|mistake|mistaken)\b", normalized):
        return _contextual_worker_message("settled", resolved_locale)
    return _contextual_worker_message("generic", resolved_locale)


def contextual_meta_fallback_reply(user_ctx: dict[str, Any] | None, *, locale: str | None = None) -> str:
    resolved_locale = LocaleManager.normalize(locale or (user_ctx or {}).get("language")).value
    grounding = (user_ctx or {}).get("conversation_grounding")
    if not isinstance(grounding, dict):
        grounding = build_conversation_grounding(user_ctx)
    topic = str(grounding.get("last_topic") or "")
    if topic == "brand_origin":
        return render_message("conversational.contextual_meta_followup.brand_origin", resolved_locale)
    if topic == "product_identity":
        return render_message("conversational.contextual_meta_followup.product_identity", resolved_locale)
    return render_message("conversational.contextual_meta_followup.generic", resolved_locale)


__all__ = [
    "CONTEXTUAL_ACTION_PROMISE_RE",
    "CONTEXTUAL_META_FOLLOWUP_INTENT",
    "CONTEXTUAL_UNGROUNDED_PREFACE_RE",
    "CONTEXTUAL_WORKER_FOLLOWUP_INTENT",
    "contextual_meta_fallback_reply",
    "contextual_worker_fallback_reply",
    "contextual_worker_grounded_reply",
]
