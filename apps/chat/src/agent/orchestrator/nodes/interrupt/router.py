from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.interrupt.context import (
    _build_interrupt_context_details,
    _state_locale,
    logger,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.input_resolve import _is_beneficiary_clarification_interrupt
from apps.chat.src.agent.orchestrator.nodes.interrupt.switch_extract import (
    _build_direct_non_transaction_switch_tasks,
    _build_enriched_transaction_switch_tasks,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.switch_updates import (
    _build_planner_switch_updates,
    _switch_updates,
)
from apps.chat.src.agent.orchestrator.nodes.planner.context import (
    build_router_context_from_summary,
    get_or_build_turn_context_summary,
)
from apps.chat.src.agent.orchestrator.services.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
    resolve_shortcut_locale,
)
from shared.types.planner import (
    InterruptRouteDecision,
    SemanticRouteDecision,
)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320

def _callback_flow_type(state: OrchestratorState) -> str | None:
    callback = state.last_callback or {}
    raw_flow_type = callback.get("flow_type")
    if not isinstance(raw_flow_type, str):
        return None
    flow_type = raw_flow_type.strip().lower()
    return flow_type or None

def _is_verified_pin_callback(state: OrchestratorState) -> bool:
    callback = state.last_callback
    if not isinstance(callback, dict):
        return False
    return bool(callback.get("pin_verified")) and state.pin_verified

def _callback_flow_matches_interrupt(
    state: OrchestratorState,
    current_task_types: set[str],
) -> tuple[bool, str | None]:
    callback_flow_type = _callback_flow_type(state)
    if not callback_flow_type:
        return True, None
    if not current_task_types:
        return False, callback_flow_type
    return callback_flow_type in current_task_types, callback_flow_type

def _is_transaction_intent(intent: str | None) -> bool:
    return bool(intent and intent in TRANSACTION_INTENTS)

def _route_fallback(reason: str) -> InterruptRouteDecision:
    return InterruptRouteDecision(
        decision="unclear",
        confidence=0.0,
        detected_language=None,
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason=reason,
    )

def _shortcut_miss_category(reason: str) -> str:
    if reason == "ambiguous":
        return "ambiguity"
    if reason == "guardrail_blocked":
        return "guardrail"
    if reason == "unsupported_locale":
        return "unsupported"
    if reason == "no_match":
        return "unsupported"
    if reason == "matched":
        return "matched"
    return "other"

def _resolve_deterministic_status_query_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language")) or resolve_shortcut_locale(
        _state_locale(state)
    )
    shortcut_route, _miss_reason = resolve_interrupt_shortcut_with_reason(
        text=text,
        interrupt_kind=interrupt.kind,
        locale=shortcut_locale,
    )
    if shortcut_route is None or shortcut_route.decision != "status_query":
        return None
    logger.info(
        "interrupt_status_query_shortcut_hit",
        kind=interrupt.kind,
        status_query_type=shortcut_route.status_query_type,
        locale=shortcut_locale.value if shortcut_locale else None,
    )
    return shortcut_route

async def _route_interrupt(
    *,
    task_planner: Any,
    state: OrchestratorState,
    text: str,
    kind: str,
    task_ids: list[str],
    current_task_types: set[str],
    fields_by_task: dict[str, list[str]],
    prompt: str | None,
) -> InterruptRouteDecision:
    if not text:
        return _route_fallback("empty_text")
    if not task_planner or not hasattr(task_planner, "route_pending_input"):
        return _route_fallback("router_unavailable")

    try:
        route_context, prompt_mode, active_task_state_mode = _build_interrupt_context_details(
            state=state,
            kind=kind,
            task_ids=task_ids,
            current_task_types=current_task_types,
            fields_by_task=fields_by_task,
            prompt=prompt,
        )
        logger.info(
            "interrupt_router_context_selected",
            prompt_mode=prompt_mode,
            active_task_state_mode=active_task_state_mode,
            task_count=len(task_ids),
            interrupt_kind=kind,
        )
        try:
            route = await task_planner.route_pending_input(
                state.phone_number,
                text,
                context=route_context,
                path_label="interrupt_path",
                prompt_mode=prompt_mode,
            )
        except TypeError:
            route = await task_planner.route_pending_input(
                state.phone_number,
                text,
                context=route_context,
            )
        route = cast(InterruptRouteDecision, route)
        logger.info(
            "interrupt_router_decision",
            kind=kind,
            decision=route.decision,
            confidence=route.confidence,
            detected_language=route.detected_language,
            target_intent=route.target_intent,
            target_mode=route.target_mode,
            status_query_type=route.status_query_type,
            prompt_mode=prompt_mode,
            active_task_state_mode=active_task_state_mode,
            task_count=len(task_ids),
        )
        return route
    except Exception as exc:
        logger.warning("interrupt_router_failed", kind=kind, error=str(exc))
        return _route_fallback("router_failed")

async def _route_interrupt_semantic_turn(
    *,
    task_planner: Any,
    state: OrchestratorState,
    text: str,
    add_task_instruction_only: bool = False,
) -> SemanticRouteDecision | None:
    if not task_planner or not hasattr(task_planner, "route_semantic_turn"):
        return None

    if add_task_instruction_only:
        semantic_context = (
            "Pending confirmation add-task instruction. Classify only this fresh user instruction. "
            "Do not reuse active pending transfer recipients, banks, or task types unless they are explicitly "
            "mentioned in the instruction."
        )
    else:
        stashed_query_session = state.stashed_query_session if isinstance(state.stashed_query_session, dict) else None
        summary, _ = get_or_build_turn_context_summary(
            state,
            query_session_snapshot=stashed_query_session,
            query_session_source="stashed" if stashed_query_session is not None else None,
            path_label="interrupt_path",
        )
        semantic_context = build_router_context_from_summary(
            summary,
            expected_executors=state.preplanner_expected_transaction_executors,
        )
    try:
        return cast(
            SemanticRouteDecision,
            await task_planner.route_semantic_turn(
                state.phone_number,
                text,
                context=semantic_context,
                path_label="interrupt_path",
            ),
        )
    except TypeError:
        return cast(
            SemanticRouteDecision,
            await task_planner.route_semantic_turn(
                state.phone_number,
                text,
                context=semantic_context,
            ),
        )
    except Exception as exc:
        logger.warning("interrupt_semantic_router_failed", error=str(exc))
        return None



def _is_same_flow_transactional_switch(
    *,
    route: InterruptRouteDecision,
    interrupt: Any,
    active_type: str,
) -> bool:
    if route.decision != "switch_intent":
        return False
    if interrupt.kind != "confirmation":
        return False
    if active_type not in TRANSACTION_INTENTS:
        return False
    return str(route.target_intent or "").strip().lower() == active_type

async def _handle_switch_intent_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    task_planner: Any,
    text: str,
    active_type: str,
    current_task_types: set[str],
    services: dict[str, Any],
) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.nodes.interrupt.reprompt import _reprompt_updates
    if _is_beneficiary_clarification_interrupt(interrupt):
        logger.info(
            "interrupt_switch_blocked",
            reason="beneficiary_disambiguation_pending",
            target_intent=route.target_intent,
            tasks=interrupt.task_ids,
        )
        return _reprompt_updates(state, interrupt)

    target_intent = (route.target_intent or "").strip().lower()
    if not target_intent or target_intent not in KNOWN_SWITCH_INTENTS:
        logger.info(
            "interrupt_switch_target_unknown",
            target_intent=target_intent or None,
            reason="missing_or_unsupported_target",
        )
        return _reprompt_updates(state, interrupt)

    if target_intent in NON_TRANSACTION_SWITCH_INTENTS:
        new_tasks, waves, new_task_types = _build_direct_non_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=target_intent,
            route=route,
        )
    else:
        semantic_route = await _route_interrupt_semantic_turn(
            task_planner=task_planner,
            state=state,
            text=text,
            add_task_instruction_only=route.target_mode == "continuation",
        )

        semantic_decision = str(getattr(semantic_route, "decision", "") or "")
        expected_executors = [
            str(item)
            for item in (getattr(semantic_route, "expected_transaction_executors", None) or [])
            if str(item) in TRANSACTION_INTENTS
        ]
        direct_transaction_domains = {
            "domain_transfer": "transfer",
            "domain_airtime": "airtime",
            "domain_data": "data",
        }
        if (
            route.target_mode == "continuation"
            and target_intent in TRANSACTION_INTENTS
            and len(set(expected_executors)) == 1
        ):
            resolved_target_intent = expected_executors[0]
        elif semantic_decision in {"planner_mixed", "planner_ambiguous"}:
            return _build_planner_switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                text=text,
                expected_executors=expected_executors,
            )
        else:
            resolved_target_intent = direct_transaction_domains.get(semantic_decision, target_intent)
        new_tasks, waves, new_task_types = await _build_enriched_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=resolved_target_intent,
            interrupt=interrupt,
            services=services,
        )
        target_intent = resolved_target_intent

    return _switch_updates(
        state=state,
        interrupt=interrupt,
        active_type=active_type,
        current_task_types=current_task_types,
        new_tasks=new_tasks,
        waves=waves,
        new_task_types=new_task_types,
        text=text,
        planner_output=None,
        primary_intent=target_intent,
        merge_with_pending_confirmation=route.target_mode == "continuation",
    )
