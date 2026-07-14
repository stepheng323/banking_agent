"""Orchestrator Graph Construction (V3)."""

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RoutingContractError,
    TurnNextStep,
)
from apps.chat.src.agent.orchestrator.workflows import (
    advance_wave,
    finalize,
    handle_pending_interrupt,
    ingest_message,
    plan_tasks,
    session_gate_direct_path,
)

CompiledOrchestratorGraph = CompiledStateGraph[
    OrchestratorState,
    None,
    OrchestratorState,
    OrchestratorState,
]


def _committed_next_step(
    state: OrchestratorState,
    *,
    allowed: frozenset[TurnNextStep],
    node: str,
) -> str:
    directive = state.turn_directive
    if directive is None:
        raise RoutingContractError(f"{node} completed without a turn directive")
    if directive.next_step not in allowed:
        raise RoutingContractError(
            f"{node} cannot transition to {directive.next_step.value} "
            f"for {directive.outcome_kind.value}"
        )
    return END if directive.next_step == TurnNextStep.END else directive.next_step.value


def _route_interrupt(state: OrchestratorState) -> Literal["advance", "plan", "finalize"] | str:
    return _committed_next_step(
        state,
        allowed=frozenset(
            {TurnNextStep.ADVANCE, TurnNextStep.PLAN, TurnNextStep.FINALIZE, TurnNextStep.END}
        ),
        node="interrupt",
    )


def _route_gate(
    state: OrchestratorState,
) -> Literal["advance", "handle_interrupt", "plan", "finalize"] | str:
    return _committed_next_step(
        state,
        allowed=frozenset(TurnNextStep),
        node="gate",
    )


def _route_plan(state: OrchestratorState) -> str:
    return _committed_next_step(
        state,
        allowed=frozenset({TurnNextStep.ADVANCE, TurnNextStep.FINALIZE, TurnNextStep.END}),
        node="planner",
    )


def _route_advance(state: OrchestratorState) -> str:
    return _committed_next_step(
        state,
        allowed=frozenset({TurnNextStep.ADVANCE, TurnNextStep.FINALIZE, TurnNextStep.END}),
        node="execution",
    )


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
        "handle_interrupt",
        _route_interrupt,
        {"advance": "advance", "plan": "plan", "finalize": "finalize", END: END},
    )
    builder.add_conditional_edges(
        "gate",
        _route_gate,
        {
            "advance": "advance",
            "handle_interrupt": "handle_interrupt",
            "plan": "plan",
            "finalize": "finalize",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "plan", _route_plan, {"advance": "advance", "finalize": "finalize", END: END}
    )
    builder.add_conditional_edges("advance", _route_advance, {"advance": "advance", "finalize": "finalize", END: END})

    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
