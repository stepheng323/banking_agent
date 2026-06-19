"""Gate workflow runner."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.core.engine import run_gate_engine
from apps.chat.src.agent.orchestrator.workflows.gate.core.runtime import build_gate_runtime
from apps.chat.src.agent.orchestrator.workflows.gate.stage_specs import GATE_STAGE_SPECS
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def session_gate_direct_path(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
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

    result = await run_gate_engine(ctx, GATE_STAGE_SPECS)
    return result.updates
