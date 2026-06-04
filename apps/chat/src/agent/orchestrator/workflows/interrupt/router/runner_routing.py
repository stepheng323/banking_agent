from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import cancel_router_fallback_reason
from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.route_decisions import _apply_interrupt_route_decision
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_core import (
    _route_interrupt,
    _shortcut_miss_category,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime


async def _route_and_apply_interrupt_decision(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any]:
    interrupt = runtime.interrupt
    shortcut_locale = runtime.state_view.shortcut_locale
    shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
        text=runtime.text,
        interrupt_kind=interrupt.kind,
        locale=shortcut_locale,
    )

    if shortcut_route is not None:
        route = shortcut_route
        logger.info(
            "interrupt_shortcut_hit",
            kind=interrupt.kind,
            decision=route.decision,
            reason=route.reason,
            status_query_type=route.status_query_type,
            locale=shortcut_locale.value if shortcut_locale else None,
        )
    else:
        logger.info(
            "interrupt_shortcut_miss",
            kind=interrupt.kind,
            locale=shortcut_locale.value if shortcut_locale else None,
            reason=miss_reason,
            miss_category=_shortcut_miss_category(miss_reason),
        )
        logger.info(
            "interrupt_cancel_router_fallback",
            kind=interrupt.kind,
            reason=cancel_router_fallback_reason(runtime.text),
        )
        route = await _route_interrupt(
            task_planner=runtime.task_planner,
            state=state,
            text=runtime.text,
            kind=interrupt.kind,
            task_ids=interrupt.task_ids,
            current_task_types=runtime.current_task_types,
            fields_by_task=interrupt.fields_by_task,
            prompt=interrupt.prompt,
        )

    return await _apply_interrupt_route_decision(
        state=state,
        interrupt=interrupt,
        route=route,
        current_task_types=runtime.current_task_types,
        task_planner=runtime.task_planner,
        text=runtime.text,
        active_type=runtime.active_type,
        services=runtime.services,
        redis_client=runtime.redis_client,
    )


__all__ = ["_route_and_apply_interrupt_decision"]
