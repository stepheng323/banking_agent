import re
import unicodedata
from typing import Any

from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.conversation.conversation_grounding import build_conversation_grounding
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_contextual import (
    contextual_meta_fallback_reply,
    contextual_worker_fallback_reply,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.outcomes import direct_response
from apps.chat.src.agent.orchestrator.workflows.gate.stages.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.workflows.gate.support_identity import _support_user_id
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_context_for_state as build_context_frame_followup_context_for_state,
)
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.models import LocaleCode
from banking.support.context_manager import SupportContextManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_SUPPORTED_CONTEXTUAL_LOCALES = {LocaleCode.EN, LocaleCode.PCM, LocaleCode.YO, LocaleCode.HA, LocaleCode.IG}
_EXACT_APPRECIATION_REPLIES: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {"thanks", "thank you", "thankyou"},
    LocaleCode.PCM: {"thanks", "thank you", "thankyou"},
    LocaleCode.YO: {"e se", "ese", "o se"},
    LocaleCode.HA: {"nagode"},
    LocaleCode.IG: {"dalu", "imela"},
}
_CONTEXTUAL_ACK_PREFIX_RE_BY_LOCALE: dict[LocaleCode, re.Pattern[str]] = {
    LocaleCode.EN: re.compile(
        r"^\s*(?:"
        r"ok(?:ay)?|alright|all right|great|nice|cool|perfect|sweet|fine|good|"
        r"got\s+it|i\s+see|ah\s+i\s+see|oh\s+okay|my\s+bad"
        r")\b",
        re.IGNORECASE,
    ),
    LocaleCode.PCM: re.compile(
        r"^\s*(?:"
        r"ok(?:ay)?|ok\s+na|no\s+wahala|i\s+don\s+see|i\s+see\s+am|"
        r"e\s+make\s+sense|sharp|correct|my\s+bad"
        r")\b",
        re.IGNORECASE,
    ),
    LocaleCode.YO: re.compile(
        r"^\s*(?:o\s+dara|ko\s+si\s+wahala|mo\s+ti\s+ri|mo\s+ti\s+ye|o\s+ye\s+mi|"
        r"mo\s+ye|okay|ok)\b",
        re.IGNORECASE,
    ),
    LocaleCode.HA: re.compile(
        r"^\s*(?:na\s+gane|na\s+fahimta|ba\s+matsala|lafiya|shi\s+kenan|okay|ok)\b",
        re.IGNORECASE,
    ),
    LocaleCode.IG: re.compile(
        r"^\s*(?:o\s+di\s+mma|enweghi\s+nsogbu|agh?otala\s+m|ahu?r?u?\s+m|okay|ok)\b",
        re.IGNORECASE,
    ),
}
_CONTEXTUAL_REACTION_RE_BY_LOCALE: dict[LocaleCode, re.Pattern[str]] = {
    LocaleCode.EN: re.compile(
        r"\b(?:"
        r"good\s+to\s+know|makes\s+sense|that\s+helps|all\s+good|"
        r"that(?:'s| is)\s+mental|that's\s+mad|that\s+is\s+mad|interesting|"
        r"my\s+bad|i\s+(?:thought|assumed|figured|was\s+thinking|was\s+worried)"
        r")\b",
        re.IGNORECASE,
    ),
    LocaleCode.PCM: re.compile(
        r"\b(?:"
        r"good\s+to\s+know|e\s+make\s+sense|all\s+good|my\s+bad|"
        r"i\s+(?:bin\s+|been\s+)?think\s+say|i\s+think\s+say"
        r")\b",
        re.IGNORECASE,
    ),
    LocaleCode.YO: re.compile(
        r"\b(?:mo\s+(?:ro|lero|ni\s+lokan)\s+pe|o\s+ye\s+mi|mo\s+ye|ko\s+si\s+wahala)\b",
        re.IGNORECASE,
    ),
    LocaleCode.HA: re.compile(
        r"\b(?:(?:na\s+(?:yi\s+)?zaton|na\s+dauka|ina\s+tunanin)|na\s+gane|na\s+fahimta|ba\s+matsala)\b",
        re.IGNORECASE,
    ),
    LocaleCode.IG: re.compile(
        r"\b(?:(?:eche(?:re)?\s+m\s+na|a\s+chere\s+m\s+na)|o\s+di\s+mma|"
        r"agh?otala\s+m|enweghi\s+nsogbu)\b",
        re.IGNORECASE,
    ),
}
_ACTIONABLE_FOLLOWUP_RE = re.compile(
    r"\b(?:"
    r"show\s+(?:me\s+)?(?:the\s+)?details?|details?|more\s+info(?:rmation)?|"
    r"retry|try\s+again|resend|send\s+again|"
    r"receipt|proof\s+of\s+payment|payment\s+proof|"
    r"status\s+of|what(?:'s| is| happened)|why|how|when|where|did|does|can|could|will|would|"
    r"send|transfer|pay|buy|purchase|recharge|top\s*up|check|show|list|view|get|"
    r"balance|statement|beneficiary|account|airtime|data|bundle|ticket|complaint|refund|reversal|"
    r"wetin\s+be\s+status|show\s+am|retry\s+am|try\s+am\s+again|send\s+receipt|"
    r"ipo|risiti|tun\s+gbiyanju|fi\s+alaye\s+han|fi\s+owo\s+ranse|"
    r"matsayi|rasit|sake\s+gwadawa|nuna|tura|aika|sayi|duba|"
    r"onodu|receipt|nwalee\s+ozo|gosi|ziga|zipu|zuta|lele"
    r")\b",
    re.IGNORECASE,
)
_ACTION_AFTER_ACK_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|alright|all\s+right|great|nice|cool|got\s+it|sure)[,.\s]+)?"
    r"(?:show|list|view|get|check|what|why|how|when|where|send|transfer|pay|buy|"
    r"purchase|recharge|top\s*up|retry|resend|create|open|raise|submit|cancel|stop|use|change|make)\b",
    re.IGNORECASE,
)


def _normalize_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_marks.casefold().strip()).strip()


def _candidate_locales(ctx: GateContext) -> list[LocaleCode]:
    loaded_context = ctx.state_view.loaded_context_or_empty
    raw_candidates = [
        ctx.current_locale,
        loaded_context.get("detected_language"),
        loaded_context.get("language"),
    ]
    locales: list[LocaleCode] = []
    for raw in raw_candidates:
        locale = LocaleManager.normalize(raw)
        if locale in _SUPPORTED_CONTEXTUAL_LOCALES and locale not in locales:
            locales.append(locale)
    if LocaleCode.EN not in locales:
        locales.append(LocaleCode.EN)
    return locales


def _has_recent_history_context(ctx: GateContext) -> bool:
    loaded_context = ctx.state_view.loaded_context_or_empty
    history = loaded_context.get("history")
    if not isinstance(history, list):
        return False
    for turn in reversed(history[-6:]):
        if not isinstance(turn, dict):
            continue
        if str(turn.get("role") or "").strip().lower() == "assistant" and str(turn.get("content") or "").strip():
            return True
    return False


def _has_state_result_context(ctx: GateContext) -> bool:
    if ctx.state_view.has_final_response:
        return True
    if ctx.state_view.has_task_results:
        return True
    if ContextFrameManager().latest_active_frame(ctx.state) is not None:
        return True
    return False


def _looks_like_contextual_worker_acknowledgement(text: str | None, locales: list[LocaleCode]) -> bool:
    normalized = _normalize_text(text).lower().rstrip("?.!,")
    if not normalized:
        return False
    if any(normalized in _EXACT_APPRECIATION_REPLIES.get(locale, set()) for locale in locales):
        return False
    if "?" in (text or ""):
        return False
    if _ACTION_AFTER_ACK_RE.search(normalized) or _ACTIONABLE_FOLLOWUP_RE.search(normalized):
        return False
    return any(
        _CONTEXTUAL_ACK_PREFIX_RE_BY_LOCALE[locale].search(normalized)
        or _CONTEXTUAL_REACTION_RE_BY_LOCALE[locale].search(normalized)
        for locale in locales
    )


async def _support_context_summary(ctx: GateContext) -> dict[str, Any] | None:
    if ctx.redis_client is None:
        return None
    try:
        support_ctx = await SupportContextManager(ctx.redis_client).get(_support_user_id(ctx.state_view))
    except Exception as exc:
        logger.warning("gate_contextual_worker_support_context_failed", error=str(exc))
        return None

    summary = {
        "last_transaction_ref": getattr(support_ctx, "last_transaction_ref", None),
        "last_ticket_id": getattr(support_ctx, "last_ticket_id", None),
        "last_issue_intent": getattr(getattr(support_ctx, "last_issue_intent", None), "value", None)
        or getattr(support_ctx, "last_issue_intent", None),
        "last_support_step": getattr(support_ctx, "last_support_step", None),
        "has_pending_reference": getattr(support_ctx, "pending_reference", None) is not None,
    }
    return {key: value for key, value in summary.items() if value not in (None, "", False)}


def _contextual_summary_text(ctx: GateContext, support_context: dict[str, Any] | None) -> str:
    parts: list[str] = []
    if ctx.turn_summary is not None:
        if ctx.turn_summary.recent_domain_focus:
            parts.append(f"recent_domain_focus={ctx.turn_summary.recent_domain_focus}")
        if ctx.turn_summary.recent_answer_focus:
            parts.append(f"recent_answer_focus={ctx.turn_summary.recent_answer_focus}")
        if ctx.turn_summary.history_lines:
            parts.append("recent_history=" + " | ".join(ctx.turn_summary.history_lines[-3:]))
    frame_context = build_context_frame_followup_context_for_state(ctx.state)
    if frame_context:
        parts.append("displayed_context=" + frame_context[:700])
    if support_context:
        parts.append("support_context=" + str(support_context))
    return "\n".join(parts)[:1200]


async def _stage_contextual_worker_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Route non-actionable acknowledgement/commentary after prior results to conversation."""
    candidate_locales = _candidate_locales(ctx)
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_gate_blocking_state
        or not _looks_like_contextual_worker_acknowledgement(ctx.message_text, candidate_locales)
    ):
        return None

    await ctx.ensure_turn_summary()
    if isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("pending_clarification"):
        return None

    support_context = await _support_context_summary(ctx)
    has_grounded_context = bool(
        _has_recent_history_context(ctx)
        or _has_state_result_context(ctx)
        or support_context
        or (ctx.turn_summary and (ctx.turn_summary.recent_domain_focus or ctx.turn_summary.recent_answer_focus))
    )
    if not has_grounded_context:
        return None

    context_summary = _contextual_summary_text(ctx, support_context)
    loaded_context = ctx.state_view.loaded_context_or_empty
    grounding = loaded_context.get("conversation_grounding")
    if not isinstance(grounding, dict):
        grounding = build_conversation_grounding(loaded_context)

    last_topic = str(grounding.get("last_topic") or "")
    if last_topic in {"brand_origin", "product_identity"}:
        responder_intent = "contextual_meta_followup"
    else:
        responder_intent = "contextual_worker_followup"

    fallback_user_ctx = {
        **loaded_context,
        "language": ctx.current_locale,
        "conversation_grounding": grounding,
    }
    if responder_intent == "contextual_worker_followup":
        fallback_user_ctx["contextual_worker_followup"] = context_summary
    extra_user_ctx: dict[str, object] = {"conversation_grounding": grounding}
    if responder_intent == "contextual_worker_followup":
        extra_user_ctx["contextual_worker_followup"] = context_summary
    reply = await _build_bounded_conversational_reply(
        ctx,
        ctx.current_locale,
        intent=responder_intent,
        extra_user_ctx=extra_user_ctx,
    )
    if reply:
        final_response = reply
    elif responder_intent == "contextual_meta_followup":
        final_response = contextual_meta_fallback_reply(fallback_user_ctx, locale=ctx.current_locale)
    elif responder_intent == "contextual_worker_followup":
        final_response = contextual_worker_fallback_reply(
            ctx.message_text,
            fallback_user_ctx,
            locale=ctx.current_locale,
        )
    else:
        final_response = contextual_worker_fallback_reply(
            ctx.message_text,
            fallback_user_ctx,
            locale=ctx.current_locale,
        )

    logger.info("gate_contextual_worker_followup_hit")
    return direct_response(
        ctx,
        response=final_response,
        owner="guardrail",
        decision=responder_intent,
        semantic_path_shape=responder_intent,
        extra_updates={
            **(ctx.summary_updates or {}),
            "conversation_topic": last_topic or "casual",
        },
        route_source=responder_intent,
        heuristic_type="guardrail_shortcut",
        heuristic_name="worker_acknowledgement",
    )


__all__ = ["_stage_contextual_worker_followup"]
