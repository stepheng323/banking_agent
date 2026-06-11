"""Cancellation policy, copy, and cleanup helpers for orchestrator workflows."""

from __future__ import annotations

import re
from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import CANCEL_PHRASES
from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.bridge import render_cancelled_prompt
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message

_TERMINAL_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}
_TRIM_CHARS = '.,!?;:"`~()[]{}'
_CANCEL_PHRASES = {
    re.sub(r"\s+", " ", phrase.strip().lower()).strip(_TRIM_CHARS)
    for phrases in CANCEL_PHRASES.values()
    for phrase in phrases
}
_CANCEL_HINT_TOKENS = {
    "cancel",
    "abort",
    "stop",
    "nevermind",
    "commot",
    "fagile",
    "dawoduro",
    "soke",
    "dakatar",
    "kagbuo",
    "kwusi",
}
_OBVIOUS_CANCEL_RE = re.compile(
    r"^(?:(?:please|pls|abeg|kindly|just)\s+)?"
    r"(?:cancel|abort|stop)"
    r"(?:\s+(?:this|it|this one|this transfer|the transfer|this transaction|the transaction|"
    r"this flow|the flow|current transfer|current transaction|current flow))?"
    r"(?:\s+(?:please|pls|abeg))?$"
)
EXPLICIT_CANCEL_PATTERNS = (
    r"\bcancel\b",
    r"\babort\b",
    r"\bstop\b",
    r"\bnevermind\b",
    r"\bnever\s+mind\b",
)


def _normalize_message(text: str | None) -> str:
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text.strip().lower())
    return compact.strip(_TRIM_CHARS)


def state_locale(state: OrchestratorState, locale_override: str | None = None) -> str:
    return LocaleManager.normalize(locale_override or (state.loaded_context or {}).get("language")).value


def cancelled_message(state: OrchestratorState, locale_override: str | None = None) -> str:
    return render_cancelled_prompt(state_locale(state, locale_override))


def clarify_message(state: OrchestratorState, locale_override: str | None = None) -> str:
    return render_message("conversational.clarify", state_locale(state, locale_override))


def _cancel_match_kind(text: str | None) -> str | None:
    normalized = _normalize_message(text)
    if not normalized:
        return None
    if normalized in _CANCEL_PHRASES:
        return "exact"
    if _OBVIOUS_CANCEL_RE.fullmatch(normalized):
        return "obvious"
    return None


def is_explicit_cancel_message(text: str | None) -> bool:
    return _cancel_match_kind(text) == "exact"


def is_obvious_cancel_message(text: str | None) -> bool:
    return _cancel_match_kind(text) is not None


def cancel_match_kind(text: str | None) -> str | None:
    return _cancel_match_kind(text)


def cancel_router_fallback_reason(text: str | None) -> str:
    normalized = _normalize_message(text)
    if not normalized:
        return "unsupported"
    tokens = set(normalized.split())
    if "never mind" in normalized or any(token in tokens for token in _CANCEL_HINT_TOKENS):
        return "ambiguous"
    return "unsupported"


def has_explicit_cancel(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in EXPLICIT_CANCEL_PATTERNS)


def has_cancelable_state(state: OrchestratorState) -> bool:
    if state.pending_interrupt is not None:
        return True
    if state.waves:
        return True
    if any(task.stage not in _TERMINAL_STAGES for task in state.tasks.values()):
        return True
    if state.session_stack:
        return True
    if state.active_domain:
        return True
    if state.stashed_query_session is not None:
        return True
    if state.stashed_sessions:
        return True
    return False


async def clear_query_session(redis_client: Any | None, phone_number: str) -> None:
    if not redis_client:
        return
    try:
        await redis_client.delete(f"query:session:{phone_number}")
    except Exception:
        # Best effort only; callers should not fail a cancellation because cleanup key deletion failed.
        return


async def build_cancellation_reset_updates(
    state: OrchestratorState,
    redis_client: Any | None,
) -> dict[str, Any]:
    await clear_query_session(redis_client, state.phone_number)
    return {
        "tasks": {},
        "waves": [],
        "current_wave_index": 0,
        "pending_interrupt": None,
        "task_results": {},
        "session_stack": [],
        "active_domain": None,
        "stashed_query_session": None,
        "stashed_sessions": [],
        "planner_output": None,
        "normalized_instruction": None,
        "pin_verified": False,
        "authorization_context": None,
        "last_callback": None,
        "preplanner_expected_transaction_executors": [],
    }


__all__ = [
    "build_cancellation_reset_updates",
    "cancel_match_kind",
    "cancel_router_fallback_reason",
    "cancelled_message",
    "clarify_message",
    "clear_query_session",
    "has_explicit_cancel",
    "has_cancelable_state",
    "is_explicit_cancel_message",
    "is_obvious_cancel_message",
    "state_locale",
]
