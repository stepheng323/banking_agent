from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.lifecycle.reducer import reduce_finalize_runtime
from apps.chat.src.agent.orchestrator.workflows.lifecycle.route_transition import finalize_turn_route
from apps.chat.src.agent.orchestrator.workflows.lifecycle.runtime import build_finalize_runtime


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Final Step. Generate response and queue receipts."""
    runtime = build_finalize_runtime(state, config)
    return finalize_turn_route(state, await reduce_finalize_runtime(runtime))
