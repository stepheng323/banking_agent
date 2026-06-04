"""Status-query handling for active interrupts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_recovery import (
    _recover_status_query_without_active_flow,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_text import (
    _build_status_query_response,
)
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from shared.types.planner import InterruptRouteDecision


async def _status_query_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    semantic_path_shape: str,
    task_planner: Any,
    text: str,
    active_type: str,
    services: OrchestrationServices,
    redis_client: Any | None,
) -> dict[str, Any]:
    if not current_task_types or not current_task_types.issubset(TRANSACTION_INTENTS):
        return await _recover_status_query_without_active_flow(
            state=state,
            interrupt=interrupt,
            route=route,
            current_task_types=current_task_types,
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            services=services,
            redis_client=redis_client,
        )

    response = _build_status_query_response(
        state=state,
        interrupt=interrupt,
        task_types=current_task_types,
        status_query_type=route.status_query_type,
    )
    logger.info(
        "interrupt_status_query_hit",
        kind=interrupt.kind,
        status_query_type=route.status_query_type or "recap",
        active_types=sorted(current_task_types),
    )
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
        "outbox": [{"type": "say", "text": response}],
        "semantic_path_shape": semantic_path_shape,
    }


__all__ = ["_status_query_updates"]
