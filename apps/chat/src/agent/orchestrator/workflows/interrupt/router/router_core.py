from typing import Any, cast

from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
    resolve_shortcut_locale,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import (
    _build_interrupt_context_details,
    _state_locale,
    logger,
)
from shared.types.planner import InterruptRouteDecision


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
    task_planner: TaskPlanner | None,
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
    if task_planner is None:
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
        route = await task_planner.route_pending_input(
            state.phone_number,
            text,
            context=route_context,
            path_label="interrupt_path",
            prompt_mode=prompt_mode,
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


__all__ = [
    "_resolve_deterministic_status_query_route",
    "_route_fallback",
    "_route_interrupt",
    "_shortcut_miss_category",
]
