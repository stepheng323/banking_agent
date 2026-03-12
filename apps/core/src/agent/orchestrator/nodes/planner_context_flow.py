"""Planner context assembly flow helpers."""

import re
from dataclasses import dataclass
from typing import Any, cast

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    PLANNER_CONTEXT_MAX_CHARS,
    _assemble_planner_context,
    _clip_text,
    _load_query_session_snapshot,
    build_turn_context_summary,
    build_user_state_summary_from_summary,
)
from apps.core.src.agent.orchestrator.nodes.planner_fastpath import (
    TRANSACTION_EXECUTORS,
)
from apps.core.src.agent.orchestrator.nodes.planner_query_shortcuts import (
    _is_query_continuation_blocked,
    _looks_like_explicit_query_continuation,
    _next_query_continuation_task_id,
)
from shared.services.task_planner_prompt_models import PlannerPromptSignals
from shared.types.planner import TransactionExecutor
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)

PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS = 640
PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS = 700
PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS = 700
PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS = 220
PLANNER_CONTEXT_USER_STATE_MAX_CHARS = 900
_TX_HINT_KEYWORDS = (
    "send",
    "transfer",
    "pay",
    "buy",
    "airtime",
    "data",
    "bundle",
    "firanse",
    "ra",
    "saya",
    "tura",
    "ziga",
    "envoye",
    "envoyer",
)
_PHONE_HINT_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_AMOUNT_HINT_PATTERN = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?\b")


@dataclass(slots=True)
class PlannerContextBuildResult:
    planner_context: str
    active_intent: str | None
    query_session_snapshot: dict[str, Any] | None
    query_session_source: str | None
    prompt_signals: PlannerPromptSignals
    shortcut_updates: dict[str, Any] | None = None


def _has_transaction_intent_hint(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False

    if any(keyword in normalized for keyword in _TX_HINT_KEYWORDS):
        return True

    if _AMOUNT_HINT_PATTERN.search(normalized):
        return True

    for candidate in _PHONE_HINT_PATTERN.findall(normalized):
        if normalize_nigerian_phone(candidate):
            return True

    # Network mentions are strong transaction hints.
    for token in re.findall(r"[A-Za-z0-9]+", normalized):
        if normalize_network_name(token) or token in {"mtn", "glo", "airtel", "9mobile"}:
            return True

    return False


async def _build_planner_context(
    *,
    state: OrchestratorState,
    text: str,
    redis_client: Any | None,
    locale_updates: dict[str, Any],
) -> PlannerContextBuildResult:
    planner_context_sections: list[tuple[str, str]] = []
    query_session_snapshot: dict[str, Any] | None = None
    query_session_source: str | None = None
    query_session_active = False
    has_short_term_memory = False
    has_user_state_summary = False
    recent_domain_focus: str | None = None
    current_flow_type: str | None = None
    if state.waves and state.current_wave_index < len(state.waves):
        current_wave = state.waves[state.current_wave_index]
        if current_wave:
            wave_task = state.tasks.get(current_wave[0])
            if wave_task:
                current_flow_type = wave_task.type
    is_transactional_flow = current_flow_type in TRANSACTION_EXECUTORS

    query_session_snapshot, query_session_source = await _load_query_session_snapshot(state, redis_client)
    if query_session_snapshot and not is_transactional_flow:
        session_active = bool(query_session_snapshot.get("session_active"))
        query_session_active = session_active
        if (
            session_active
            and state.pending_interrupt is None
            and _looks_like_explicit_query_continuation(text)
            and not _is_query_continuation_blocked(text)
        ):
            shortcut_task_id = _next_query_continuation_task_id(state.tasks)
            shortcut_task = TaskSpec(
                id=shortcut_task_id,
                type="query",
                stage=TaskStage.DRAFT,
                payload={
                    "action": "transaction_list",
                    "instruction": text,
                    "message": text,
                },
            )
            logger.info("planner_query_continuation_shortcut_hit", message=text)
            return PlannerContextBuildResult(
                planner_context="None",
                active_intent=None,
                query_session_snapshot=query_session_snapshot,
                query_session_source=query_session_source,
                prompt_signals=PlannerPromptSignals(),
                shortcut_updates={
                    "tasks": {shortcut_task_id: shortcut_task},
                    "waves": [[shortcut_task_id]],
                    "current_wave_index": 0,
                    "normalized_instruction": text,
                    **locale_updates,
                },
            )
    elif query_session_snapshot and is_transactional_flow:
        logger.info("planner_query_context_skipped", reason="active_transaction_flow")

    active_intent = None
    if state.waves:
        try:
            current_wave = state.waves[state.current_wave_index]
            if current_wave:
                t_id = current_wave[0]
                if t_id in state.tasks:
                    active_task = state.tasks[t_id]
                    active_intent = active_task.type
        except Exception as e:
            logger.warning("active_flow_context_failed", error=str(e))

    turn_summary = build_turn_context_summary(
        state,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
    )
    if turn_summary.query_session_summary and not is_transactional_flow:
        section_name = "query_session" if query_session_source == "redis" else "query_session_stashed"
        planner_context_sections.append(
            (
                section_name,
                _clip_text(turn_summary.query_session_summary, PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS),
            )
        )
        logger.info("planner_context_injected", context=section_name)

    if turn_summary.active_flow_summary and active_intent:
        planner_context_sections.append(
            (
                "active_flow",
                _clip_text(turn_summary.active_flow_summary, PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS),
            )
        )
        logger.info("planner_context_active_flow_injected", intent=active_intent)

    if turn_summary.short_term_memory_summary:
        has_short_term_memory = True
        planner_context_sections.append(
            (
                "short_term_memory",
                _clip_text(turn_summary.short_term_memory_summary, PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS),
            )
        )
        logger.info("planner_context_injected", context="short_term_memory")

    recent_domain_focus = turn_summary.recent_domain_focus
    if recent_domain_focus:
        planner_context_sections.append(
            (
                "recent_domain_focus",
                _clip_text(
                    (
                        f"Recent Domain Focus: {recent_domain_focus}\n"
                        "- Referential/underspecified follow-ups should keep this domain.\n"
                        "- Switch only when user clearly asks another domain."
                    ),
                    PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS,
                ),
            )
        )
        logger.info("planner_context_injected", context="recent_domain_focus", domain=recent_domain_focus)

    if turn_summary.recent_answer_focus:
        planner_context_sections.append(
            (
                "recent_answer_focus",
                _clip_text(
                    (
                        f"Recent Answer Focus: {turn_summary.recent_answer_focus}\n"
                        "- Prefer grounding short referential follow-ups against this recent surface first."
                    ),
                    PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS,
                ),
            )
        )
        logger.info("planner_context_injected", context="recent_answer_focus", focus=turn_summary.recent_answer_focus)

    user_state_summary = build_user_state_summary_from_summary(turn_summary)
    if user_state_summary:
        has_user_state_summary = True
        planner_context_sections.append(
            ("user_state_history", _clip_text(user_state_summary, PLANNER_CONTEXT_USER_STATE_MAX_CHARS))
        )
        logger.info("planner_context_injected", context="user_state_history")

    planner_context, included_sections, clipped_sections, dropped_sections = _assemble_planner_context(
        planner_context_sections,
        max_chars=PLANNER_CONTEXT_MAX_CHARS,
    )
    logger.info(
        "planner_context_size",
        chars=len(planner_context),
        truncated=bool(clipped_sections),
        sections=len(included_sections),
        clipped_sections=clipped_sections,
        dropped_sections=dropped_sections,
    )
    expected_executors = tuple(
        cast(TransactionExecutor, item)
        for item in state.preplanner_expected_transaction_executors
        if item in TRANSACTION_EXECUTORS
    )
    prompt_signals = PlannerPromptSignals(
        active_flow_type=active_intent,
        pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
        query_session_active=query_session_active,
        query_session_source=query_session_source,
        recent_domain_focus=recent_domain_focus,
        has_beneficiary_suggestion=False,
        has_user_state_summary=has_user_state_summary,
        has_short_term_memory=has_short_term_memory,
        has_quote=state.has_quote and bool(state.quoted_message_id),
        has_transaction_intent_hint=_has_transaction_intent_hint(text),
        expected_transaction_executors=expected_executors,
    )

    return PlannerContextBuildResult(
        planner_context=planner_context,
        active_intent=active_intent,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
        prompt_signals=prompt_signals,
    )


__all__ = [
    "PlannerContextBuildResult",
    "_build_planner_context",
]
