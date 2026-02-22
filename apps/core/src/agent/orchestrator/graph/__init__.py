"""Orchestrator Graph Construction (V3)."""

from typing import Any, Literal, cast

from langgraph.graph import END, StateGraph

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes import (
    advance_wave,
    finalize,
    handle_pending_interrupt,
    ingest_message,
    plan_tasks,
    session_gate_fastpath,
)


def build_orchestrator_graph(checkpointer: Any = None) -> Any:
    """Build the top-level Orchestrator Graph (V3)."""
    builder = StateGraph(OrchestratorState)

    builder.add_node("ingest", ingest_message)
    builder.add_node("handle_interrupt", handle_pending_interrupt)
    builder.add_node("gate", session_gate_fastpath)
    builder.add_node("plan", plan_tasks)
    builder.add_node("advance", advance_wave)
    builder.add_node("finalize", finalize)

    builder.set_entry_point("ingest")

    builder.add_edge("ingest", "gate")
    def route_interrupt(state: OrchestratorState) -> Literal["advance", "plan"] | str:
        if state.final_response:
            return cast(str, END)
        if state.pending_interrupt:
            return cast(str, END)
        if state.current_wave_index < len(state.waves):
            return "advance"
        return "plan"

    builder.add_conditional_edges("handle_interrupt", route_interrupt, {"advance": "advance", "plan": "plan", END: END})

    def route_gate(state: OrchestratorState) -> Literal["advance", "handle_interrupt", "plan"]:
        if state.fast_path_triggered:
            return "advance"
        if state.pending_interrupt:
            return "handle_interrupt"
        return "plan"

    builder.add_conditional_edges(
        "gate", route_gate, {"advance": "advance", "handle_interrupt": "handle_interrupt", "plan": "plan"}
    )

    from shared.utils.logging import get_logger

    logger = get_logger(__name__)

    def route_plan(state: OrchestratorState) -> str:
        logger.info("route_plan_check", final_response=state.final_response)
        if state.final_response:
            return cast(str, END)
        return "advance"

    builder.add_conditional_edges("plan", route_plan, {"advance": "advance", END: END})

    def route_advance(state: OrchestratorState) -> str:
        if state.pending_interrupt:
            return cast(str, END)
        if state.current_wave_index >= len(state.waves):
            return "finalize"
        return "advance"

    builder.add_conditional_edges("advance", route_advance, {"advance": "advance", "finalize": "finalize", END: END})

    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
