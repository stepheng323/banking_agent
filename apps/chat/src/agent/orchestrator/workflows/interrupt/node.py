from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import RoutingContractError
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.outcome import resolve_interrupt_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.runner_preflight import (
    _resolve_pre_router_interrupt_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.runner_routing import (
    _route_and_apply_interrupt_decision,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import build_interrupt_runtime
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.presentation.i18n.renderer import render_message
from shared.observability.llm import LLMCallDeadlineExceeded


async def handle_pending_interrupt(state: OrchestratorState, config: RunnableConfig) -> dict[str, object]:
    """Process user input against the pending interrupt (if any)."""
    state_view = interrupt_state_view(state)
    interrupt = state_view.pending_interrupt
    if not interrupt:
        raise RoutingContractError("interrupt workflow requires a live pending interrupt")

    logger.info("handling_interrupt", kind=interrupt.kind, tasks=interrupt.task_ids)
    runtime = build_interrupt_runtime(state=state, config=config)

    try:
        pre_router_updates = await _resolve_pre_router_interrupt_updates(state=state, runtime=runtime)
        if pre_router_updates is not None:
            return pre_router_updates.materialize()
        routed_resolution = await _route_and_apply_interrupt_decision(state=state, runtime=runtime)
        return routed_resolution.materialize()
    except LLMCallDeadlineExceeded as exc:
        logger.warning(
            "interrupt_llm_deadline_exceeded",
            role=exc.role,
            deadline_seconds=exc.deadline_seconds,
        )
        updates = _reprompt_updates(state, interrupt)
        updates["outbox"] = [
            {
                "type": "say",
                "text": render_message("orchestrator.fallback.router_timeout", state_view.current_locale),
            }
        ]
        return resolve_interrupt_updates(
            state,
            updates,
            decision="interrupt_router_timeout",
        ).materialize()
