"""Deterministic status and account-switch interrupt shortcuts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_core import (
    _resolve_deterministic_status_query_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import _handle_switch_intent_route
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    TRANSACTION_INTENTS,
    _is_account_balance_interrupt_switch,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_flow import _status_query_updates
from shared.types.planner import InterruptRouteDecision


async def _deterministic_status_query_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    route = _resolve_deterministic_status_query_route(
        state=state,
        interrupt=runtime.interrupt,
        text=runtime.text,
    )
    if route is None:
        return None
    return await _status_query_updates(
        state=state,
        interrupt=runtime.interrupt,
        route=route,
        current_task_types=runtime.current_task_types,
        path_shape="interrupt_deterministic",
        task_planner=runtime.task_planner,
        text=runtime.text,
        active_type=runtime.active_type,
        services=runtime.services,
        redis_client=runtime.redis_client,
    )


async def _account_balance_switch_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if not (
        interrupt.kind in {"confirmation", "auth"}
        and runtime.current_task_types
        and runtime.current_task_types.issubset(TRANSACTION_INTENTS)
        and _is_account_balance_interrupt_switch(runtime.text)
    ):
        return None

    logger.info(
        "interrupt_deterministic_account_balance_switch",
        kind=interrupt.kind,
        active_type=runtime.active_type,
        tasks=interrupt.task_ids,
    )
    return await _handle_switch_intent_route(
        state=state,
        interrupt=interrupt,
        route=InterruptRouteDecision(
            decision="switch_intent",
            confidence=1.0,
            detected_language=None,
            target_intent="account",
            target_mode="new",
            reason="deterministic account balance request during transaction interrupt",
        ),
        task_planner=runtime.task_planner,
        text=runtime.text,
        active_type=runtime.active_type,
        current_task_types=runtime.current_task_types,
        services=runtime.services,
    )


__all__ = [
    "_account_balance_switch_updates",
    "_deterministic_status_query_updates",
]
