from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    CONTEXT_ACCOUNT_PREVIEW_LIMIT,
    PLANNER_CONTEXT_MAX_CHARS,
    _assemble_planner_context,
    _build_query_session_context,
    _build_user_state_summary,
)
from apps.core.src.agent.orchestrator.nodes.planner_fastpath import TRANSACTION_EXECUTORS
from apps.core.src.agent.orchestrator.nodes.planner_guardrails import (
    _deescalate_mandate_acknowledgement,
    _filter_spurious_affirmation_tasks,
)
from apps.core.src.agent.orchestrator.nodes.planner_policy import (
    _build_locale_update,
    _build_policy_aware_greeting,
    _build_policy_notice,
    _detected_locale_value,
    _meta_intent_from_response_key,
)
from apps.core.src.agent.orchestrator.nodes.planner_postprocess import (
    _expand_underproduced_transfer_tasks,
    _should_replan_active_wave,
    _strip_transactional_depends_on_edges,
)
from apps.core.src.agent.orchestrator.nodes.planner_quoted_replay import (
    QUOTED_REPLAY_MIN_CONFIDENCE as _QUOTED_REPLAY_MIN_CONFIDENCE,
)
from apps.core.src.agent.orchestrator.nodes.planner_quoted_replay import (
    _build_quoted_replay_context,
    _build_quoted_replay_context_with_payload,
    _build_quoted_replay_execution_updates,
    _load_quoted_actionable_payload,
    _quoted_replay_clarify_response,
)
from apps.core.src.agent.orchestrator.nodes.planner_sections import (
    _build_non_task_response,
    _build_planner_context,
    _execute_planner_with_context,
    _retry_expected_executors_if_needed,
)
from apps.core.src.agent.orchestrator.utils.task_payload import build_task_spec_from_plan_item
from apps.core.src.agent.orchestrator.utils.waves import build_dependency_waves
from shared.i18n import LocaleManager, render_message, render_safe_capability_fallback
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SAFE_CAPABILITY_FALLBACK = render_safe_capability_fallback("en")
QUOTED_REPLAY_MIN_CONFIDENCE = _QUOTED_REPLAY_MIN_CONFIDENCE


async def plan_tasks(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Planner Node.

    1. If new request (no active waves), call Planner to create TaskSpecs.
    2. If existing waves, this is a pass-through (or bulk extraction update).
    """
    if state.waves and state.pending_interrupt is None and not _should_replan_active_wave(state):
        return {}

    task_planner = config["configurable"].get("task_planner")
    text = state.last_message_text or ""
    current_locale = LocaleManager.normalize(state.loaded_context.get("language")).value
    redis_client = config["configurable"].get("redis_client")
    if task_planner is None:
        logger.error("task_planner_missing")
        return {"final_response": render_safe_capability_fallback(current_locale)}

    locale_updates = _build_locale_update(state, current_locale)

    if state.has_quote and state.quoted_message_id and hasattr(task_planner, "interpret_quoted_replay"):
        quoted_payload = await _load_quoted_actionable_payload(state, config)
        quoted_context = (
            _build_quoted_replay_context_with_payload(state, quoted_payload)
            if quoted_payload is not None
            else _build_quoted_replay_context(state)
        )
        try:
            interpretation = cast(
                QuotedReplayInterpretation,
                await task_planner.interpret_quoted_replay(state.phone_number, text, context=quoted_context),
            )
            if interpretation.decision == "clarify":
                logger.info("quoted_replay_shortcut_clarify", reason=interpretation.reason)
                return {
                    "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                    "normalized_instruction": text,
                    **locale_updates,
                }
            if interpretation.decision == "execute":
                if interpretation.confidence < QUOTED_REPLAY_MIN_CONFIDENCE:
                    logger.info(
                        "quoted_replay_confidence_low",
                        decision=interpretation.decision,
                        confidence=interpretation.confidence,
                        min_confidence=QUOTED_REPLAY_MIN_CONFIDENCE,
                    )
                    return {
                        "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                        "normalized_instruction": text,
                        **locale_updates,
                    }
                if quoted_payload is None:
                    logger.info("quoted_replay_actionable_payload_missing", quoted_message_id=state.quoted_message_id)
                    return {
                        "final_response": render_message("conversational.clarify", current_locale),
                        "normalized_instruction": text,
                        **locale_updates,
                    }
                replay_updates = _build_quoted_replay_execution_updates(
                    state=state,
                    text=text,
                    interpretation=interpretation,
                    locale_updates=locale_updates,
                )
                if replay_updates is not None:
                    return replay_updates
                logger.info(
                    "quoted_replay_insufficient_payload",
                    decision=interpretation.decision,
                    reason=interpretation.reason,
                )
                return {
                    "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                    "normalized_instruction": text,
                    **locale_updates,
                }
            logger.info("quoted_replay_shortcut_miss", decision=interpretation.decision, reason=interpretation.reason)
        except Exception as exc:
            logger.warning("quoted_replay_shortcut_failed", error=str(exc))

    context_result = await _build_planner_context(
        state=state,
        text=text,
        redis_client=redis_client,
        locale_updates=locale_updates,
    )
    if context_result.shortcut_updates is not None:
        return context_result.shortcut_updates

    planner_context = context_result.planner_context
    active_intent = context_result.active_intent
    query_session_snapshot = context_result.query_session_snapshot
    query_session_source = context_result.query_session_source

    try:
        execution_result = await _execute_planner_with_context(
            state=state,
            task_planner=task_planner,
            text=text,
            planner_context=planner_context,
            active_intent=active_intent,
            current_locale=current_locale,
            redis_client=redis_client,
        )
    except Exception as e:
        logger.error("planner_failed", error=str(e))
        return {}

    planner_output = execution_result.planner_output
    current_locale = execution_result.current_locale
    fastpath_context_updates = execution_result.fastpath_context_updates

    handled_response = await _build_non_task_response(
        state=state,
        planner_output=planner_output,
        text=text,
        task_planner=task_planner,
        redis_client=redis_client,
        active_intent=active_intent,
        current_locale=current_locale,
        locale_updates=locale_updates,
        fastpath_context_updates=fastpath_context_updates,
    )
    if handled_response is not None:
        return handled_response

    if state.waves and active_intent:
        logger.info("planner_intent_switch_or_update", old=active_intent, new=planner_output.primary_intent)

    fanout_tasks, fanout_meta = _expand_underproduced_transfer_tasks(planner_output.tasks, text)
    if fanout_meta:
        planner_output.tasks = fanout_tasks
        planner_output.is_complex = True
        logger.info(
            "planner_transfer_multi_recipient_fanout_applied",
            source_task_id=fanout_meta["source_task_id"],
            recipient_count=fanout_meta["recipient_count"],
            recipient_names=fanout_meta["recipient_names"],
        )

    normalized_tasks, stripped_edges = _strip_transactional_depends_on_edges(planner_output.tasks)
    if stripped_edges:
        planner_output.tasks = normalized_tasks
        logger.info(
            "txn_dep_removed_for_batch_auth",
            source_task_ids=sorted({source for source, _ in stripped_edges}),
            target_task_ids=sorted({target for _, target in stripped_edges}),
            removed_edges=[f"{source}->{target}" for source, target in stripped_edges],
            removed_count=len(stripped_edges),
        )

    planner_output = await _retry_expected_executors_if_needed(
        state=state,
        task_planner=task_planner,
        planner_output=planner_output,
        text=text,
        planner_context=planner_context,
        active_intent=active_intent,
        current_locale=current_locale,
    )

    stashed_query_session_update: dict[str, Any] | None = None
    if (
        query_session_source == "redis"
        and query_session_snapshot
        and bool(query_session_snapshot.get("session_active"))
        and any(getattr(task, "executor", None) in TRANSACTION_EXECUTORS for task in planner_output.tasks)
    ):
        stash_keys = (
            "session_active",
            "query_contract",
            "query_result",
            "surface",
            "show_expanded",
            "current_page",
            "page_size",
            "account_id",
            "account_ids",
            "cached_transactions",
            "cache_fetched_at",
            "cache_fingerprint",
            "timestamp",
        )
        stashed_query_session_update = {
            key: query_session_snapshot.get(key) for key in stash_keys if key in query_session_snapshot
        }
        stashed_query_session_update["session_active"] = True
        logger.info(
            "planner_query_session_stashed_for_transaction_switch",
            keys=list(stashed_query_session_update.keys()),
        )

    new_tasks = {}
    task_ids: list[str] = []
    depends_on_by_task: dict[str, list[str]] = {}

    for plan_item in planner_output.tasks:
        spec = build_task_spec_from_plan_item(
            plan_item,
            text,
            preserve_existing_action_instruction=True,
            include_skip_extraction=True,
            strip_transfer_recipient_suffix=True,
            format_narration_requires_recipient_field=False,
        )
        new_tasks[spec.id] = spec
        task_ids.append(spec.id)
        depends_on_by_task[spec.id] = list(spec.depends_on)

    waves = build_dependency_waves(task_ids, depends_on_by_task)

    policy_notice = _build_policy_notice(text, planner_output, current_locale)
    if policy_notice:
        logger.info("policy_notice_created")

    return {
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": planner_output,
        "policy_notice": policy_notice,
        "stashed_query_session": (
            stashed_query_session_update if stashed_query_session_update else state.stashed_query_session
        ),
        **locale_updates,
    }


__all__ = [
    "CONTEXT_ACCOUNT_PREVIEW_LIMIT",
    "PLANNER_CONTEXT_MAX_CHARS",
    "QUOTED_REPLAY_MIN_CONFIDENCE",
    "SAFE_CAPABILITY_FALLBACK",
    "_assemble_planner_context",
    "_build_policy_aware_greeting",
    "_build_policy_notice",
    "_build_query_session_context",
    "_build_user_state_summary",
    "_deescalate_mandate_acknowledgement",
    "_detected_locale_value",
    "_filter_spurious_affirmation_tasks",
    "_meta_intent_from_response_key",
    "plan_tasks",
]
