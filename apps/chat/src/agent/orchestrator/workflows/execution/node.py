from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import RoutingContractError, TurnDirective
from apps.chat.src.agent.orchestrator.workflows.execution.route_transition import translate_execution_route
from apps.chat.src.agent.orchestrator.workflows.execution.wave.engine import run_execution_wave


async def advance_wave(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Execution Node.

    Iterates through tasks in current wave.
    Invokes Domain Workers.
    Aggregates outcomes and sets PendingInterrupt if blocked.
    """
    if not isinstance(state.turn_directive, TurnDirective):
        raise RoutingContractError("execution wave requires an existing turn directive")
    result = await run_execution_wave(state, config)
    return translate_execution_route(state, result.updates)
