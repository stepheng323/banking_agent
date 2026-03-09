"""Planner runtime section helpers."""

from dataclasses import dataclass
from typing import Any, cast

from apps.core.src.agent.orchestrator.meta_reply import generate_meta_reply
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
    CONTEXT_FASTPATH_FLOW_SUBTYPES,
    NO_ACTIVE_FLOW_FASTPATH_MESSAGE,
    TRANSACTION_EXECUTORS,
    _build_beneficiary_fastpath_context_updates,
    _build_fastpath_fallback_task,
    _context_fastpath_shown_limit,
    _context_fastpath_total_items,
    _has_context_for_fastpath_subtype,
    _infer_recent_domain_focus,
    _planner_fastpath_subtype,
)
from apps.core.src.agent.orchestrator.nodes.planner_guardrails import (
    _deescalate_mandate_acknowledgement,
    _filter_spurious_affirmation_tasks,
)
from apps.core.src.agent.orchestrator.nodes.planner_policy import (
    _build_locale_update,
    _build_policy_aware_greeting,
    _detected_locale_value,
    _meta_intent_from_response_key,
)
from apps.core.src.agent.orchestrator.nodes.planner_query_shortcuts import (
    _is_query_continuation_blocked,
    _looks_like_explicit_query_continuation,
    _next_query_continuation_task_id,
)
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.i18n import (
    LanguageDetectionSignal,
    LocaleManager,
    MessageKey,
    render_message,
    render_safe_capability_fallback,
    render_text,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

PLANNER_CONTEXT_BENEFICIARY_SUGGESTION_MAX_CHARS = 420
PLANNER_CONTEXT_QUERY_SESSION_MAX_CHARS = 640
PLANNER_CONTEXT_ACTIVE_FLOW_MAX_CHARS = 700
PLANNER_CONTEXT_SHORT_TERM_MAX_CHARS = 700
PLANNER_CONTEXT_RECENT_DOMAIN_MAX_CHARS = 220
PLANNER_CONTEXT_USER_STATE_MAX_CHARS = 900


@dataclass(slots=True)
class PlannerContextBuildResult:
    planner_context: str
    active_intent: str | None
    query_session_snapshot: dict[str, Any] | None
    query_session_source: str | None
    shortcut_updates: dict[str, Any] | None = None


@dataclass(slots=True)
class PlannerExecutionResult:
    planner_output: Any
    current_locale: str
    fastpath_context_updates: dict[str, Any]


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
    return PlannerContextBuildResult(
        planner_context=planner_context,
        active_intent=active_intent,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
    )


async def _execute_planner_with_context(
    *,
    state: OrchestratorState,
    task_planner: Any,
    text: str,
    planner_context: str,
    active_intent: str | None,
    current_locale: str,
    redis_client: Any | None,
) -> PlannerExecutionResult:
    planner_output = await task_planner.plan_tasks(state.phone_number, text, context=planner_context)
    planner_output = _filter_spurious_affirmation_tasks(
        planner_output,
        active_intent=active_intent,
        pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
    )
    planner_output = _deescalate_mandate_acknowledgement(
        planner_output,
        loaded_context=state.loaded_context,
        locale=current_locale,
    )
    logger.info("planner_tasks_generated", output=planner_output)

    fastpath_subtype = _planner_fastpath_subtype(planner_output)
    if fastpath_subtype:
        has_context_for_fastpath = _has_context_for_fastpath_subtype(state, fastpath_subtype)
        has_no_tasks = not planner_output.tasks
        is_conversational_no_task = planner_output.primary_intent == "conversational" and has_no_tasks
        is_flow_fastpath = fastpath_subtype in CONTEXT_FASTPATH_FLOW_SUBTYPES

        if is_conversational_no_task and has_context_for_fastpath:
            logger.info("context_fastpath_hit", subtype=fastpath_subtype)
            total_items = _context_fastpath_total_items(state, fastpath_subtype)
            shown_limit = _context_fastpath_shown_limit(fastpath_subtype)
            if total_items is not None and total_items > shown_limit:
                logger.info(
                    "context_fastpath_list_truncated",
                    subtype=fastpath_subtype,
                    shown=shown_limit,
                    total=total_items,
                )
        else:
            if not has_context_for_fastpath:
                fallback_reason = "insufficient_context"
            elif has_no_tasks:
                fallback_reason = "invalid_no_task_shape"
            else:
                fallback_reason = "planner_emitted_task"

            if has_no_tasks:
                fallback_task = _build_fastpath_fallback_task(fastpath_subtype, text)
                if fallback_task:
                    planner_output.tasks = [fallback_task]
                    planner_output.primary_intent = fallback_task.executor
                    planner_output.is_complex = False
                    planner_output.response = ""
                    planner_output.response_key = None
                    logger.info(
                        "context_fastpath_fallback_to_worker",
                        subtype=fastpath_subtype,
                        reason=fallback_reason,
                    )
                elif is_flow_fastpath and not has_context_for_fastpath:
                    planner_output.primary_intent = "conversational"
                    planner_output.tasks = []
                    planner_output.is_complex = False
                    planner_output.response_key = None
                    if not planner_output.response:
                        planner_output.response = NO_ACTIVE_FLOW_FASTPATH_MESSAGE
                    logger.info("interrupt_status_query_no_active_flow", subtype=fastpath_subtype)
            else:
                logger.info(
                    "context_fastpath_fallback_to_worker",
                    subtype=fastpath_subtype,
                    reason=fallback_reason,
                )

    detected_language = getattr(planner_output, "detected_language", None)
    if detected_language:
        if redis_client:
            signal = LanguageDetectionSignal(
                locale=LocaleManager.from_detection(detected_language),
                confidence=float(getattr(planner_output, "confidence", 1.0) or 0.0),
                source="planner",
                explicit=False,
            )
            resolved_locale = await LocaleManager.update_locale(state.phone_number, signal)
            current_locale = resolved_locale.value
        else:
            current_locale = LocaleManager.from_detection(detected_language).value

    if redis_client and planner_context != "None" and planner_output and planner_output.tasks:
        is_saving = any(t.executor == "beneficiary" and t.action == "save_beneficiary" for t in planner_output.tasks)
        if not is_saving:
            suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
            await redis_client.delete(suggestion_key)
            logger.info("cleared_stale_beneficiary_context", phone=state.phone_number)

    fastpath_context_updates = _build_beneficiary_fastpath_context_updates(
        state,
        planner_output,
        fastpath_subtype,
    )
    return PlannerExecutionResult(
        planner_output=planner_output,
        current_locale=current_locale,
        fastpath_context_updates=fastpath_context_updates,
    )


async def _retry_expected_executors_if_needed(
    *,
    state: OrchestratorState,
    task_planner: Any,
    planner_output: Any,
    text: str,
    planner_context: str,
    active_intent: str | None,
    current_locale: str,
) -> Any:
    expected_executors = {
        str(item)
        for item in (state.preplanner_expected_transaction_executors or [])
        if str(item) in TRANSACTION_EXECUTORS
    }
    if not expected_executors:
        return planner_output

    planned_executors = {task.executor for task in planner_output.tasks if task.executor in TRANSACTION_EXECUTORS}
    missing_executors = sorted(expected_executors - planned_executors)
    if not missing_executors:
        return planner_output

    retry_context = _clip_text(
        (
            f"{planner_context}\n\nPre-planner expected explicit transaction executors: "
            f"{', '.join(sorted(expected_executors))}. "
            f"Ensure all explicit executors are represented in tasks."
        ),
        PLANNER_CONTEXT_MAX_CHARS,
    )
    try:
        retry_output = await task_planner.plan_tasks(state.phone_number, text, context=retry_context)
        retry_output = _filter_spurious_affirmation_tasks(
            retry_output,
            active_intent=active_intent,
            pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
        )
        retry_output = _deescalate_mandate_acknowledgement(
            retry_output,
            loaded_context=state.loaded_context,
            locale=current_locale,
        )
        retry_executors = {task.executor for task in retry_output.tasks if task.executor in TRANSACTION_EXECUTORS}
        if expected_executors.issubset(retry_executors):
            logger.info(
                "planner_expected_executor_retry_applied",
                expected_executors=sorted(expected_executors),
                missing_executors=missing_executors,
            )
            return retry_output
        logger.warning(
            "planner_expected_executor_retry_rejected",
            expected_executors=sorted(expected_executors),
            retry_executors=sorted(retry_executors),
        )
    except Exception as exc:
        logger.warning(
            "planner_expected_executor_retry_failed",
            error=str(exc),
            expected_executors=sorted(expected_executors),
            missing_executors=missing_executors,
        )
    return planner_output


async def _build_non_task_response(
    *,
    state: OrchestratorState,
    planner_output: Any,
    text: str,
    task_planner: Any,
    redis_client: Any | None,
    active_intent: str | None,
    current_locale: str,
    locale_updates: dict[str, Any],
    fastpath_context_updates: dict[str, Any],
) -> dict[str, Any] | None:
    detected_locale = _detected_locale_value(planner_output)

    def _localized_planner_response(raw_response: str | None) -> str:
        if not raw_response:
            return ""
        return cast(str, render_text(raw_response, current_locale))

    if getattr(planner_output, "is_cancellation", False) or planner_output.primary_intent == "cancel":
        logger.info("planner_cancellation_detected", intent=planner_output.primary_intent)
        cancel_locale = detected_locale or current_locale
        cancel_locale_updates = locale_updates if cancel_locale == current_locale else _build_locale_update(
            state, cancel_locale
        )
        if planner_output.response_key == "planner.cancelled":
            logger.info("planner_response_key_used", key=planner_output.response_key, locale=cancel_locale)
            cancel_message = render_message(planner_output.response_key, cancel_locale)
        else:
            cancel_message = _localized_planner_response(planner_output.response) or render_message(
                "planner.cancelled", cancel_locale
            )
        return {
            "waves": [],
            "final_response": cancel_message,
            **cancel_locale_updates,
        }

    if planner_output and planner_output.tasks:
        return None

    if planner_output and planner_output.primary_intent == "conversational":
        conversational_locale = detected_locale or current_locale
        conversational_locale_updates = (
            locale_updates
            if conversational_locale == current_locale
            else _build_locale_update(state, conversational_locale)
        )
        if planner_output.response:
            logger.info("planner_direct_response_used", locale=conversational_locale)
            return {
                "final_response": _localized_planner_response(planner_output.response),
                **conversational_locale_updates,
                **fastpath_context_updates,
            }

        response_key = planner_output.response_key
        if response_key:
            logger.info("planner_response_key_used", key=response_key, locale=conversational_locale)
            if response_key == "conversational.greeting":
                return {
                    "final_response": _build_policy_aware_greeting(conversational_locale),
                    **conversational_locale_updates,
                    **fastpath_context_updates,
                }

            meta_intent = _meta_intent_from_response_key(response_key)
            llm = getattr(task_planner, "planner_llm", None)
            if meta_intent and llm is not None and hasattr(llm, "with_structured_output"):
                meta_message, handoff = await generate_meta_reply(
                    llm,
                    user_message=text,
                    user_language_hint=conversational_locale,
                    meta_intent=meta_intent,
                    redis_client=redis_client,
                )
                if handoff == "meta" and meta_message:
                    logger.info(
                        "meta_query_route_hit",
                        source="response_key",
                        meta_kind=meta_intent.value,
                    )
                    logger.info(
                        "planner_meta_reply_used",
                        response_key=response_key,
                        locale=conversational_locale,
                        intent=meta_intent.value,
                    )
                    return {
                        "final_response": meta_message,
                        **conversational_locale_updates,
                        **fastpath_context_updates,
                    }
                logger.info(
                    "planner_meta_reply_fallback",
                    response_key=response_key,
                    locale=conversational_locale,
                    handoff=handoff,
                )
            return {
                "final_response": render_message(response_key, conversational_locale),
                **conversational_locale_updates,
                **fastpath_context_updates,
            }

        logger.info(
            "planner_response_key_missing_and_no_response",
            intent=planner_output.primary_intent,
            detected_language=getattr(planner_output, "detected_language", None),
        )
        fallback_key: MessageKey = "conversational.clarify"
        logger.info("conversational_fallback_deterministic_used", key=fallback_key, locale=conversational_locale)
        return {
            "final_response": render_message(fallback_key, conversational_locale),
            **conversational_locale_updates,
            **fastpath_context_updates,
        }

    if state.waves and planner_output and planner_output.primary_intent != "conversational":
        if planner_output.primary_intent != active_intent:
            logger.info("planner_switch_empty_tasks", old=active_intent, new=planner_output.primary_intent)
            return {
                "waves": [],
                "final_response": _localized_planner_response(planner_output.response),
                **locale_updates,
                **fastpath_context_updates,
            }

    if planner_output and planner_output.response:
        return {
            "final_response": _localized_planner_response(planner_output.response),
            **locale_updates,
            **fastpath_context_updates,
        }
    return {
        "final_response": render_safe_capability_fallback(current_locale),
        **locale_updates,
        **fastpath_context_updates,
    }


__all__ = [
    "_build_non_task_response",
    "_build_planner_context",
    "_execute_planner_with_context",
    "_retry_expected_executors_if_needed",
]
