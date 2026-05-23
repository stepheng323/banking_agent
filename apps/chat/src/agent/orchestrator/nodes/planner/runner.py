from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner.context import (
    CONTEXT_ACCOUNT_PREVIEW_LIMIT,
    PLANNER_CONTEXT_MAX_CHARS,
    _assemble_planner_context,
    _build_query_session_context,
    _build_user_state_summary,
)
from apps.chat.src.agent.orchestrator.nodes.planner.context_flow import _build_planner_context
from apps.chat.src.agent.orchestrator.nodes.planner.execution_flow import _execute_planner_with_context
from apps.chat.src.agent.orchestrator.nodes.planner.guardrails import (
    _deescalate_mandate_acknowledgement,
    _filter_spurious_affirmation_tasks,
)
from apps.chat.src.agent.orchestrator.nodes.planner.policy import (
    _build_locale_update,
    _build_policy_aware_greeting,
    _build_policy_notice,
    _detected_locale_value,
    _meta_intent_from_response_key,
)
from apps.chat.src.agent.orchestrator.nodes.planner.postprocess import _should_replan_active_wave
from apps.chat.src.agent.orchestrator.nodes.planner.quoted_flow import _handle_quoted_replay_shortcut
from apps.chat.src.agent.orchestrator.nodes.planner.quoted_replay import (
    QUOTED_REPLAY_MIN_CONFIDENCE as _QUOTED_REPLAY_MIN_CONFIDENCE,
)
from apps.chat.src.agent.orchestrator.nodes.planner.response_flow import _build_non_task_response
from apps.chat.src.agent.orchestrator.nodes.planner.task_flow import _build_planner_task_updates
from apps.chat.src.agent.shared.routing_signals import looks_like_transaction_replay_modifier_request
from shared.i18n import LocaleManager, render_safe_capability_fallback
from shared.types.planner import PlannedTask, TaskParameters
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SAFE_CAPABILITY_FALLBACK = render_safe_capability_fallback("en")
QUOTED_REPLAY_MIN_CONFIDENCE = _QUOTED_REPLAY_MIN_CONFIDENCE
_UNEXPECTED_ROUTE_RECOVERY_MIN_CONFIDENCE = 0.5

_PLANNER_DOMAIN_TARGETS = {"query", "account", "support", "beneficiary", "transfer", "airtime", "data", "schedule"}
_PLANNER_TRANSFER_PREFIXES = ("send", "transfer", "pay", "remit")


def _planner_route_updates(
    *,
    decision: str,
    planner_output: Any | None = None,
    target_domain: str | None = None,
) -> dict[str, Any]:
    resolved_target = target_domain
    if resolved_target is None and planner_output is not None:
        primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
        if primary_intent in _PLANNER_DOMAIN_TARGETS:
            resolved_target = primary_intent
    return {
        "routing_owner": "planner",
        "routing_decision": decision,
        "routing_target_domain": resolved_target,
        "planner_used": True,
    }


def _recover_unexpected_question_task(planner_output: Any, text: str) -> PlannedTask | None:
    if not planner_output or getattr(planner_output, "tasks", None):
        return None

    primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
    if primary_intent not in {"faq", "support"}:
        return None

    confidence = float(getattr(planner_output, "confidence", 0.0) or 0.0)
    if confidence < _UNEXPECTED_ROUTE_RECOVERY_MIN_CONFIDENCE:
        return None

    instruction = str(getattr(planner_output, "normalized_instruction", "") or "").strip() or text
    if not instruction:
        return None

    action = "answer_question" if primary_intent == "faq" else "handle_request"
    return PlannedTask(
        task_id=f"{primary_intent}_unexpected_question",
        action=action,
        executor=primary_intent,
        instruction=instruction,
        parameters=TaskParameters(),
        risk="READ_ONLY",
    )


def _looks_like_amount_only_transfer_start(text: str) -> bool:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return False
    if not any(normalized.startswith(prefix) for prefix in _PLANNER_TRANSFER_PREFIXES):
        return False
    if not any(char.isdigit() for char in normalized):
        return False
    if any(marker in normalized for marker in (" and ", " then ", ",")):
        return False
    if any(keyword in normalized for keyword in ("airtime", "data", "bundle", "balance", "transaction", "statement")):
        return False
    return True


def _recover_missing_slot_transfer_task(planner_output: Any, text: str) -> PlannedTask | None:
    if not planner_output or getattr(planner_output, "tasks", None):
        return None
    if not _looks_like_amount_only_transfer_start(text):
        return None

    primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
    if primary_intent not in {"conversational", "transfer"}:
        return None

    return PlannedTask(
        task_id="transfer_missing_slots_recovery",
        action="send_money",
        executor="transfer",
        instruction=text,
        parameters=TaskParameters(),
        risk="MONEY_MOVE",
    )


def _recover_replay_modifier_transfer_task(planner_output: Any, text: str) -> PlannedTask | None:
    if not looks_like_transaction_replay_modifier_request(text):
        return None
    if not planner_output:
        return None

    tasks = list(getattr(planner_output, "tasks", None) or [])
    primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
    if tasks:
        non_support_tasks = [task for task in tasks if getattr(task, "executor", None) != "support"]
        if non_support_tasks:
            return None
    elif primary_intent not in {"support", "conversational"}:
        return None

    return PlannedTask(
        task_id="transfer_replay_modifier_recovery",
        action="send_money",
        executor="transfer",
        instruction=text,
        parameters=TaskParameters(),
        risk="MONEY_MOVE",
    )


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
        return {
            "final_response": render_safe_capability_fallback(current_locale),
            **_planner_route_updates(decision="planner_unavailable"),
        }

    locale_updates = _build_locale_update(state, current_locale)

    quoted_replay_updates = await _handle_quoted_replay_shortcut(
        state=state,
        config=config,
        task_planner=task_planner,
        text=text,
        current_locale=current_locale,
        locale_updates=locale_updates,
        quoted_replay_min_confidence=QUOTED_REPLAY_MIN_CONFIDENCE,
    )
    if quoted_replay_updates is not None:
        return {
            **quoted_replay_updates,
            **_planner_route_updates(decision="quoted_replay"),
        }

    context_result = await _build_planner_context(
        state=state,
        text=text,
        redis_client=redis_client,
        locale_updates=locale_updates,
        task_planner=task_planner,
    )
    if context_result.shortcut_updates is not None:
        return {
            **context_result.shortcut_updates,
            "semantic_path_shape": context_result.shortcut_updates.get("semantic_path_shape") or "planner",
            **_planner_route_updates(decision="planner_context_read"),
        }

    planner_context = context_result.planner_context
    active_intent = context_result.active_intent
    query_session_snapshot = context_result.query_session_snapshot
    query_session_source = context_result.query_session_source
    prompt_signals = context_result.prompt_signals

    try:
        execution_result = await _execute_planner_with_context(
            state=state,
            task_planner=task_planner,
            text=text,
            planner_context=planner_context,
            prompt_signals=prompt_signals,
            active_intent=active_intent,
            current_locale=current_locale,
            redis_client=redis_client,
        )
    except Exception as e:
        logger.error("planner_failed", error=str(e))
        return _planner_route_updates(decision="planner_failed")

    planner_output = execution_result.planner_output
    current_locale = execution_result.current_locale
    context_read_updates = execution_result.context_read_updates

    locale_updates = _build_locale_update(state, current_locale)

    recovered_replay_task = _recover_replay_modifier_transfer_task(planner_output, text)
    if recovered_replay_task is not None:
        planner_output.tasks = [recovered_replay_task]
        planner_output.primary_intent = "transfer"
        planner_output.response = ""
        planner_output.response_key = None
        logger.info(
            "unexpected_turn_route_breadcrumb",
            user_turn_kind=str(getattr(planner_output, "primary_intent", "unknown") or "unknown"),
            active_session_present=bool(state.session_stack),
            selected_route="transfer_replay_modifier_recovery",
            route_reason="planner_support_replay_modifier_recovery",
            policy_blocked=False,
            fallback_path="worker_task_injected",
        )
    else:
        recovered_task = _recover_unexpected_question_task(planner_output, text)
        if recovered_task is not None:
            planner_output.tasks = [recovered_task]
            planner_output.response = ""
            planner_output.response_key = None
            logger.info(
                "unexpected_turn_route_breadcrumb",
                user_turn_kind=planner_output.primary_intent,
                active_session_present=bool(state.session_stack),
                selected_route=f"{planner_output.primary_intent}_task",
                route_reason="planner_primary_intent_no_task_recovery",
                policy_blocked=False,
                fallback_path="worker_task_injected",
            )
        else:
            recovered_transfer_task = _recover_missing_slot_transfer_task(planner_output, text)
            if recovered_transfer_task is not None:
                planner_output.tasks = [recovered_transfer_task]
                planner_output.primary_intent = "transfer"
                planner_output.response = ""
                planner_output.response_key = None
                logger.info(
                    "unexpected_turn_route_breadcrumb",
                    user_turn_kind=str(getattr(planner_output, "primary_intent", "unknown") or "unknown"),
                    active_session_present=bool(state.session_stack),
                    selected_route="transfer_missing_slot_recovery",
                    route_reason="planner_amount_only_transfer_recovery",
                    policy_blocked=False,
                    fallback_path="worker_task_injected",
                )

    handled_response = await _build_non_task_response(
        state=state,
        planner_output=planner_output,
        text=text,
        redis_client=redis_client,
        active_intent=active_intent,
        current_locale=current_locale,
        locale_updates=locale_updates,
        context_read_updates=context_read_updates,
        conversation_responder=config["configurable"].get("conversation_responder"),
    )
    if handled_response is not None:
        return {
            **handled_response,
            "semantic_path_shape": handled_response.get("semantic_path_shape") or "planner",
            **_planner_route_updates(
                decision=str(getattr(planner_output, "primary_intent", "") or "planner_non_task_response"),
                planner_output=planner_output,
            ),
        }

    if state.waves and active_intent:
        logger.info("planner_intent_switch_or_update", old=active_intent, new=planner_output.primary_intent)

    task_updates = await _build_planner_task_updates(
        planner_output=planner_output,
        text=text,
        locale=current_locale,
        query_session_source=query_session_source,
        query_session_snapshot=query_session_snapshot,
    )
    planner_output = task_updates["planner_output"]
    new_tasks = task_updates["new_tasks"]
    waves = task_updates["waves"]
    stashed_query_session_update = task_updates["stashed_query_session_update"]
    if task_updates.get("capability_block_response"):
        return {
            "final_response": task_updates["capability_block_response"],
            "normalized_instruction": text,
            "planner_output": planner_output,
            "semantic_path_shape": "planner_capability_blocked",
            **_planner_route_updates(decision="capability_blocked", planner_output=planner_output),
            **locale_updates,
        }
    if task_updates.get("batch_limit_response"):
        return {
            "final_response": task_updates["batch_limit_response"],
            "normalized_instruction": text,
            "planner_output": planner_output,
            "semantic_path_shape": "planner",
            **_planner_route_updates(decision="transaction_batch_limit", planner_output=planner_output),
            **locale_updates,
        }

    policy_notice = _build_policy_notice(text, planner_output, current_locale)
    capability_policy_notice = task_updates.get("capability_policy_notice")
    if policy_notice and capability_policy_notice:
        policy_notice = f"{capability_policy_notice}\n\n{policy_notice}"
    elif capability_policy_notice:
        policy_notice = capability_policy_notice
    if policy_notice:
        logger.info("policy_notice_created")

    return {
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": planner_output,
        "policy_notice": policy_notice,
        "semantic_path_shape": "planner",
        "stashed_query_session": (
            stashed_query_session_update if stashed_query_session_update else state.stashed_query_session
        ),
        **_planner_route_updates(
            decision=str(getattr(planner_output, "primary_intent", "") or "planner_task_plan"),
            planner_output=planner_output,
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
