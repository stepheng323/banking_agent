"""Orchestrator Graph Construction (V3)."""

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows import (
    advance_wave,
    finalize,
    handle_pending_interrupt,
    ingest_message,
    plan_tasks,
    session_gate_direct_path,
)
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

CompiledOrchestratorGraph = CompiledStateGraph[
    OrchestratorState,
    None,
    OrchestratorState,
    OrchestratorState,
]


def _route_interrupt(state: OrchestratorState) -> Literal["advance", "plan"] | str:
    if state.final_response:
        return END
    if state.pending_interrupt:
        return END
    if state.current_wave_index < len(state.waves):
        return "advance"
    return "plan"


def _route_gate(state: OrchestratorState) -> Literal["advance", "handle_interrupt", "plan"]:
    if state.direct_path_triggered:
        return "advance"
    if state.pending_interrupt:
        return "handle_interrupt"
    return "plan"


def _route_plan(state: OrchestratorState) -> str:
    logger.info(
        "route_plan_check",
        has_final_response=bool(state.final_response),
        final_response_hash=log_fingerprint(state.final_response),
    )
    if state.final_response:
        return END
    return "advance"


def _route_advance(state: OrchestratorState) -> str:
    if state.pending_interrupt:
        return END
    if state.current_wave_index >= len(state.waves):
        return "finalize"
    return "advance"


def build_orchestrator_graph(checkpointer: Checkpointer = None) -> CompiledOrchestratorGraph:
    """Build the top-level Orchestrator Graph."""
    builder: StateGraph[OrchestratorState, None, OrchestratorState, OrchestratorState] = StateGraph(OrchestratorState)

    builder.add_node("ingest", ingest_message)
    builder.add_node("handle_interrupt", handle_pending_interrupt)
    builder.add_node("gate", session_gate_direct_path)
    builder.add_node("plan", plan_tasks)
    builder.add_node("advance", advance_wave)
    builder.add_node("finalize", finalize)

    builder.set_entry_point("ingest")

    builder.add_edge("ingest", "gate")

    builder.add_conditional_edges(
        "handle_interrupt", _route_interrupt, {"advance": "advance", "plan": "plan", END: END}
    )
    builder.add_conditional_edges(
        "gate", _route_gate, {"advance": "advance", "handle_interrupt": "handle_interrupt", "plan": "plan"}
    )
    builder.add_conditional_edges("plan", _route_plan, {"advance": "advance", END: END})
    builder.add_conditional_edges("advance", _route_advance, {"advance": "advance", "finalize": "finalize", END: END})

    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
