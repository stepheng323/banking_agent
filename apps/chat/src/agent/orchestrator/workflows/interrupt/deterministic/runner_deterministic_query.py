"""Deterministic read-only query interrupt shortcuts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import _is_query_domain_request
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import _handle_switch_intent_route
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from shared.types.planner import InterruptRouteDecision


async def _standalone_query_switch_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if not (
        interrupt.kind == "input"
        and runtime.current_task_types
        and runtime.current_task_types.issubset(TRANSACTION_INTENTS)
        and _is_query_domain_request(runtime.text)
    ):
        return None

    logger.info(
        "interrupt_deterministic_query_switch",
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
            target_intent="query",
            target_mode="new",
            reason="deterministic read-only query request during transaction input interrupt",
        ),
        task_planner=runtime.task_planner,
        text=runtime.text,
        active_type=runtime.active_type,
        current_task_types=runtime.current_task_types,
        services=runtime.services,
    )


__all__ = ["_standalone_query_switch_updates"]
