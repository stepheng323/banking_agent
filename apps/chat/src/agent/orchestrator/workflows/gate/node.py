"""Gate workflow runner."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from apps.chat.src.agent.orchestrator.workflows.gate.runtime import build_gate_runtime
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def session_gate_direct_path(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.workflows.gate.registry import (
        _GATE_STAGES,
        _stage_stale_interrupt_cleanup,
    )

    """
    Direct-path gate.

    Applies deterministic guardrails first, then delegates first-pass semantic routing
    to the semantic router, and falls through to planner only for planner-owned routes.

    Architecture: a pipeline of focused stage functions. Each stage returns a dict
    (short-circuit with a gate response) or None (continue to the next stage).
    """
    runtime = build_gate_runtime(state, config)
    logger.info(
        "gate_entry",
        session_domain=runtime.state_view.active_session_domain,
        interrupt=runtime.state_view.has_pending_interrupt,
    )
    ctx = runtime.build_context()

    _stage_stale_interrupt_cleanup(ctx)

    for stage in _GATE_STAGES:
        result = await stage(ctx)
        if result is not None:
            return result

    logger.info("gate_dispatch_to_planner", reason="planner_owned_or_unresolved_route")
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **_route_observability_updates(owner="planner", decision="planner_handoff"),
    }
