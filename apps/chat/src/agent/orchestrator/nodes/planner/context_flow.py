"""Planner context assembly flow helpers."""

import re
from dataclasses import dataclass
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner.context import (
    PLANNER_CONTEXT_MAX_CHARS,
    PLANNER_CONTEXT_SECTION_SEPARATOR,
    _assemble_planner_context,
    _clip_text,
    _derive_recent_answer_focus,
    _load_query_session_snapshot,
    build_user_state_summary_from_summary,
    get_or_build_turn_context_summary,
)
from apps.chat.src.agent.orchestrator.nodes.planner.context_frame_followup import (
    build_context_frame_followup_context,
    build_context_frame_followup_response,
)
from apps.chat.src.agent.orchestrator.nodes.planner.context_read import (
    TRANSACTION_EXECUTORS,
    _infer_recent_domain_focus,
)
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.services.task_planner_prompt_models import PlannerPromptSignals
from shared.types.planner import RouterDomainIntent, TransactionExecutor
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


def _should_use_minimal_planner_context(
    *,
    state: OrchestratorState,
    active_intent: str | None,
    query_session_active: bool,
    recent_domain_focus: str | None,
    recent_answer_focus: str | None,
    has_transaction_intent_hint: bool,
) -> bool:
    """Skip full turn-context assembly when no live state needs preservation."""
    if state.pending_interrupt is not None:
        return False
    if state.has_quote:
        return False
    if state.session_stack:
        return False
    if active_intent is not None:
        return False
    if query_session_active:
        return False
    if recent_domain_focus is not None:
        return False
    if recent_answer_focus is not None:
        return False
    if has_transaction_intent_hint:
        return False
    return True


def _is_narrow_transfer_replan(
    *,
    state: OrchestratorState,
    active_intent: str | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> bool:
    if state.has_quote:
        return False
    if query_session_active:
        return False
    if state.pending_interrupt is None:
        return False
    if active_intent != "transfer":
        task_ids = getattr(state.pending_interrupt, "task_ids", None) or []
        active_interrupt_types = {
            state.tasks[task_id].type
            for task_id in task_ids
            if isinstance(task_id, str) and task_id in state.tasks
        }
        if active_interrupt_types != {"transfer"} and state.routing_target_domain != "transfer":
            return False
    return expected_executors in {(), ("transfer",)}


def _forced_domain_owner(
    state: OrchestratorState,
    *,
    active_intent: str | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> RouterDomainIntent | None:
    if _is_narrow_transfer_replan(
        state=state,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    ):
        return "transfer"
    if state.pending_interrupt is not None:
        return None
    if state.has_quote:
        return None
    if state.direct_path_triggered:
        return None
    if state.routing_owner != "guardrail":
        return None
    if state.routing_target_domain != "transfer":
        return None
    if tuple(state.preplanner_expected_transaction_executors) != ("transfer",):
        return None
    if state.routing_decision not in {"batch_transfer_command", "account_aware_transfer_command"}:
        return None
    return "transfer"


def _should_use_compact_transaction_context(
    *,
    state: OrchestratorState,
    active_intent: str | None,
    forced_domain_owner: RouterDomainIntent | None,
    expected_executors: tuple[TransactionExecutor, ...],
    query_session_active: bool,
) -> bool:
    if state.has_quote:
        return False
    if query_session_active:
        return False
    if not expected_executors:
        return False
    if any(executor not in TRANSACTION_EXECUTORS for executor in expected_executors):
        return False
    if forced_domain_owner == "transfer":
        return True
    if len(expected_executors) >= 2:
        return True
    if state.pending_interrupt is None:
        return False
    if active_intent in TRANSACTION_EXECUTORS:
        return True
    return state.routing_target_domain in TRANSACTION_EXECUTORS


async def _build_planner_context(
    *,
    state: OrchestratorState,
    text: str,
    redis_client: Any | None,
    locale_updates: dict[str, Any],
    task_planner: Any | None = None,
) -> PlannerContextBuildResult:
    frame = OrchestratorContextManager().latest_active_frame(state)
    if frame and callable(getattr(task_planner, "interpret_context_frame_followup", None)):
        try:
            decision = await task_planner.interpret_context_frame_followup(
                state.phone_number,
                text,
                context=build_context_frame_followup_context(frame),
                path_label="planner_path",
            )
        except Exception as exc:
            logger.warning("context_frame_followup_interpreter_failed", error=str(exc))
        else:
            frame_followup = build_context_frame_followup_response(state, text, decision=decision)
            logger.info(
                "context_frame_followup_decision",
                decision=decision.decision,
                confidence=decision.confidence,
                detected_language=decision.detected_language,
                reason=decision.reason,
                resolved=bool(frame_followup),
            )
            if frame_followup:
                logger.info(
                    "context_frame_followup_hit",
                    frame_type=frame.frame_type.value,
                    item_count=len(frame.items),
                )
                return PlannerContextBuildResult(
                    planner_context="None",
                    active_intent=None,
                    query_session_snapshot=None,
                    query_session_source=None,
                    prompt_signals=PlannerPromptSignals(
                        active_flow_type=None,
                        pending_interrupt_kind=None,
                        query_session_active=False,
                        query_session_source=None,
                        recent_domain_focus=frame_followup.recent_domain_focus,
                        has_beneficiary_suggestion=False,
                        has_user_state_summary=False,
                        has_short_term_memory=True,
                        has_quote=False,
                        has_transaction_intent_hint=False,
                        forced_domain_owner=None,
                        expected_transaction_executors=(),
                    ),
                    shortcut_updates={
                        "semantic_path_shape": frame_followup.semantic_path_shape,
                        "context_frames": frame_followup.context_frames or state.context_frames,
                        **({"final_response": frame_followup.response} if frame_followup.response else {}),
                        **({"tasks": frame_followup.tasks} if frame_followup.tasks else {}),
                        **({"waves": frame_followup.waves} if frame_followup.waves else {}),
                        **({"current_wave_index": 0} if frame_followup.waves else {}),
                        **locale_updates,
                    },
                )

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

    recent_domain_focus = _infer_recent_domain_focus(state)
    recent_answer_focus = _derive_recent_answer_focus(state)
    has_transaction_intent_hint = _has_transaction_intent_hint(text)
    expected_executors = tuple(
        cast(TransactionExecutor, item)
        for item in state.preplanner_expected_transaction_executors
        if item in TRANSACTION_EXECUTORS
    )
    forced_domain_owner = _forced_domain_owner(
        state,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    narrow_transfer_replan = _is_narrow_transfer_replan(
        state=state,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    compact_transaction_context = narrow_transfer_replan or _should_use_compact_transaction_context(
        state=state,
        active_intent=active_intent,
        forced_domain_owner=forced_domain_owner,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    if _should_use_minimal_planner_context(
        state=state,
        active_intent=active_intent,
        query_session_active=query_session_active,
        recent_domain_focus=recent_domain_focus,
        recent_answer_focus=recent_answer_focus,
        has_transaction_intent_hint=has_transaction_intent_hint,
    ):
        logger.info("planner_context_skipped", mode="minimal")
        return PlannerContextBuildResult(
            planner_context="None",
            active_intent=active_intent,
            query_session_snapshot=query_session_snapshot,
            query_session_source=query_session_source,
            prompt_signals=PlannerPromptSignals(
                active_flow_type=active_intent,
                pending_interrupt_kind=None,
                query_session_active=False,
                query_session_source=query_session_source,
                recent_domain_focus=None,
                has_beneficiary_suggestion=False,
                has_user_state_summary=False,
                has_short_term_memory=False,
                has_quote=False,
                has_transaction_intent_hint=has_transaction_intent_hint,
                compact_context=True,
                forced_domain_owner=forced_domain_owner,
                expected_transaction_executors=expected_executors,
            ),
        )

    turn_summary, _ = get_or_build_turn_context_summary(
        state,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
        path_label="planner_path",
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

    if turn_summary.short_term_memory_summary and not compact_transaction_context:
        has_short_term_memory = True
        planner_context_sections.append(
            (
                "short_term_memory",
                _clip_text(turn_summary.short_term_memory_summary, PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS),
            )
        )
        logger.info("planner_context_injected", context="short_term_memory")

    recent_domain_focus = turn_summary.recent_domain_focus
    if recent_domain_focus and not compact_transaction_context:
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

    if turn_summary.recent_answer_focus and not compact_transaction_context:
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

    user_state_summary = (
        build_user_state_summary_from_summary(turn_summary) if not compact_transaction_context else None
    )
    if user_state_summary:
        has_user_state_summary = True
        planner_context_sections.append(
            ("user_state_history", _clip_text(user_state_summary, PLANNER_CONTEXT_USER_STATE_MAX_CHARS))
        )
        logger.info("planner_context_injected", context="user_state_history")

    raw_chars = sum(len(content) for _, content in planner_context_sections)
    if len(planner_context_sections) > 1:
        raw_chars += len(PLANNER_CONTEXT_SECTION_SEPARATOR) * (len(planner_context_sections) - 1)
    planner_context, included_sections, clipped_sections, dropped_sections = _assemble_planner_context(
        planner_context_sections,
        max_chars=PLANNER_CONTEXT_MAX_CHARS,
    )
    logger.info(
        "planner_context_size",
        chars=len(planner_context),
        raw_chars=raw_chars,
        truncated=bool(clipped_sections),
        sections=len(included_sections),
        included_sections=included_sections,
        clipped_sections=clipped_sections,
        dropped_sections=dropped_sections,
    )
    prompt_signals = PlannerPromptSignals(
        active_flow_type=active_intent,
        pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
        query_session_active=query_session_active,
        query_session_source=query_session_source,
        recent_domain_focus=None if compact_transaction_context else recent_domain_focus,
        has_beneficiary_suggestion=False,
        has_user_state_summary=has_user_state_summary,
        has_short_term_memory=has_short_term_memory,
        has_quote=state.has_quote and bool(state.quoted_message_id),
        has_transaction_intent_hint=_has_transaction_intent_hint(text),
        compact_context=compact_transaction_context,
        forced_domain_owner=forced_domain_owner,
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
