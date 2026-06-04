from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.runner_preflight import (
    _resolve_pre_router_interrupt_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.runner_routing import (
    _route_and_apply_interrupt_decision,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import build_interrupt_runtime
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view


async def handle_pending_interrupt(state: OrchestratorState, config: RunnableConfig) -> dict[str, object]:
    """Process user input against the pending interrupt (if any)."""
    state_view = interrupt_state_view(state)
    interrupt = state_view.pending_interrupt
    if not interrupt:
        return {}

    logger.info("handling_interrupt", kind=interrupt.kind, tasks=interrupt.task_ids)
    runtime = build_interrupt_runtime(state=state, config=config)

    pre_router_updates = await _resolve_pre_router_interrupt_updates(state=state, runtime=runtime)
    if pre_router_updates is not None:
        return pre_router_updates
    return await _route_and_apply_interrupt_decision(state=state, runtime=runtime)
