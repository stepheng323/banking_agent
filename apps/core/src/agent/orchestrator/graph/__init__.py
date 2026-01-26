"""Orchestrator Graph Construction (V3)."""

from langgraph.graph import END, StateGraph

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes import (
    advance_wave,
    finalize,
    handle_pending_interrupt,
    ingest_message,
    plan_tasks,
)


def build_orchestrator_graph(checkpointer=None):
    """Build the top-level Orchestrator Graph (V3)."""
    builder = StateGraph(OrchestratorState)

    builder.add_node("ingest", ingest_message)
    builder.add_node("handle_interrupt", handle_pending_interrupt)
    builder.add_node("plan", plan_tasks)
    builder.add_node("advance", advance_wave)
    builder.add_node("finalize", finalize)

    builder.set_entry_point("ingest")

    builder.add_edge("ingest", "handle_interrupt")
    builder.add_edge("handle_interrupt", "plan")

    from shared.utils.logging import get_logger

    logger = get_logger(__name__)

    def route_plan(state: OrchestratorState):
        logger.info("route_plan_check", final_response=state.final_response)
        if state.final_response:
            return END
        return "advance"

    builder.add_conditional_edges("plan", route_plan, {"advance": "advance", END: END})

    def route_advance(state: OrchestratorState):
        if state.pending_interrupt:
            return END
        if state.current_wave_index >= len(state.waves):
            return "finalize"
        return "advance"

    builder.add_conditional_edges("advance", route_advance, {"advance": "advance", "finalize": "finalize", END: END})

    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
