"""Planner context assembly flow helpers."""

import re
from dataclasses import dataclass
from typing import Any, cast

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    PLANNER_CONTEXT_MAX_CHARS,
    _assemble_planner_context,
    _build_query_session_context,
    _build_user_state_summary,
    _clip_text,
    _compact_payload_for_prompt,
)
from apps.core.src.agent.orchestrator.nodes.planner_fastpath import (
    TRANSACTION_EXECUTORS,
    _infer_recent_domain_focus,
)
from apps.core.src.agent.orchestrator.nodes.planner_query_shortcuts import (
    _is_query_continuation_blocked,
    _looks_like_explicit_query_continuation,
    _next_query_continuation_task_id,
)
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.services.task_planner_prompt_models import PlannerPromptSignals
from shared.types.planner import TransactionExecutor
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)

PLANNER_CONTEXT_BENEFICIARY_SUGGESTION_MAX_CHARS = 420
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
    has_beneficiary_suggestion = False
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

    if redis_client:
        try:
            import asyncio
            import json

            suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
            query_session_key = f"query:session:{state.phone_number}"
            suggestion_data, query_session_data = await asyncio.gather(
                redis_client.get(suggestion_key),
                redis_client.get(query_session_key),
            )

            if suggestion_data:
                data = json.loads(suggestion_data)
                name = data.get("recipient_name") or data.get("alias_suggested") or "Unknown"
                has_beneficiary_suggestion = True
                planner_context_sections.append(
                    (
                        "beneficiary_suggestion",
                        _clip_text(
                            (
                                f"Active Context: User was asked to save beneficiary '{name}'.\n"
                                f"- Reply 'yes'/'save' -> Save with name '{name}'.\n"
                                "- Reply with explicit alias intent (e.g., 'save as Mum')"
                                " -> Save with that alias.\n"
                                "- Greetings/check-ins/thanks are NOT save intent."
                            ),
                            PLANNER_CONTEXT_BENEFICIARY_SUGGESTION_MAX_CHARS,
                        ),
                    )
                )
                logger.info("planner_context_injected", context="beneficiary_suggestion")

            if query_session_data:
                session = json.loads(query_session_data)
                if isinstance(session, dict):
                    query_session_snapshot = session
                    query_session_source = "redis"

            if query_session_snapshot and not is_transactional_flow:
                session = query_session_snapshot
                session_active = bool(session.get("session_active"))
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

                summary_text = None
                query_result = session.get("query_result")
                if isinstance(query_result, dict):
                    summary_text = query_result.get("summary_text")
                planner_context_sections.append(
                    (
                        "query_session",
                        _clip_text(
                            _build_query_session_context(
                                cast(str | None, summary_text) if isinstance(summary_text, str) else None
                            ),
                            PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS,
                        ),
                    )
                )
                logger.info("planner_context_injected", context="query_session")
            elif query_session_snapshot and is_transactional_flow:
                logger.info("planner_query_context_skipped", reason="active_transaction_flow")
        except Exception as e:
            logger.warning("planner_context_check_failed", error=str(e))

    if query_session_snapshot is None and isinstance(state.stashed_query_session, dict):
        query_session_snapshot = dict(state.stashed_query_session)
        query_session_source = "stashed"

    if query_session_snapshot and query_session_source == "stashed" and not is_transactional_flow:
        query_session_active = bool(query_session_snapshot.get("session_active"))
        summary_text = None
        query_result = query_session_snapshot.get("query_result")
        if isinstance(query_result, dict):
            summary_text = query_result.get("summary_text")
        planner_context_sections.append(
            (
                "query_session_stashed",
                _clip_text(
                    _build_query_session_context(
                        cast(str | None, summary_text) if isinstance(summary_text, str) else None
                    ),
                    PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS,
                ),
            )
        )
        logger.info("planner_context_injected", context="query_session_stashed")
    elif query_session_snapshot and query_session_source == "stashed" and is_transactional_flow:
        logger.info("planner_query_context_skipped", reason="active_transaction_flow_stashed")

    active_intent = None
    if state.waves:
        try:
            current_wave = state.waves[state.current_wave_index]
            if current_wave:
                t_id = current_wave[0]
                if t_id in state.tasks:
                    active_task = state.tasks[t_id]
                    active_intent = active_task.type

                    payload_view = {
                        k: v for k, v in active_task.payload.items() if k not in ["result", "error", "confirmation"]
                    }
                    payload_preview = _compact_payload_for_prompt(payload_view)

                    planner_context_sections.append(
                        (
                            "active_flow",
                            _clip_text(
                                (
                                    f"Active Flow: {active_intent.upper()} (User is currently in this flow).\n"
                                    f"Current Task Data: {payload_preview}\n"
                                    "Review Rule 9 (CONTEXT OVERRIDE):"
                                    f"- Slot-filling/updates keep intent='{active_intent}'.\n"
                                    "- Clearly unrelated asks switch intent."
                                ),
                                PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS,
                            ),
                        )
                    )
                    logger.info("planner_context_active_flow_injected", intent=active_intent)
        except Exception as e:
            logger.warning("active_flow_context_failed", error=str(e))

    ctx_manager = OrchestratorContextManager()
    short_term_context = ctx_manager.build_llm_summary(state)
    if short_term_context:
        has_short_term_memory = True
        planner_context_sections.append(
            ("short_term_memory", _clip_text(short_term_context, PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS))
        )
        logger.info("planner_context_injected", context="short_term_memory")

    recent_domain_focus = _infer_recent_domain_focus(state)
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

    user_state_summary = _build_user_state_summary(state)
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
        has_beneficiary_suggestion=has_beneficiary_suggestion,
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
