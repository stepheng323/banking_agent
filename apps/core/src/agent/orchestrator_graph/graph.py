"""Orchestrator Graph Construction."""

from langgraph.graph import END, StateGraph

from apps.core.src.agent.orchestrator_graph.nodes import (
    advance_or_finish,
    auth_gate,
    execute_wave_parallel,
    plan_or_resume,
    preflight_wave,
    session_gate,
    summary_node,
)
from apps.core.src.agent.orchestrator_graph.state import OrchestratorState


def build_orchestrator_graph():
    """Build the top-level Orchestrator Graph."""
    builder = StateGraph(OrchestratorState)

    # --- Nodes ---
    builder.add_node("session_gate", session_gate)
    builder.add_node("plan_or_resume", plan_or_resume)
    builder.add_node("preflight", preflight_wave)
    builder.add_node("auth_gate", auth_gate)
    builder.add_node("execute", execute_wave_parallel)
    builder.add_node("advance", advance_or_finish)
    builder.add_node("summary", summary_node)

    # --- Edges ---
    builder.set_entry_point("session_gate")

    # Session Gate Routing
    def route_session(state: OrchestratorState):
        # We check flags or return values from session_gate
        # But here we rely on state inspection since node was async and modified state?
        # Ideally node returns a directive, but mapped to state flags is Pattern A.
        # Let's assume session_gate sets a temporary "_route" or we check state.
        
        # If cancelled (logic in gate probably clears state, so maybe we check for clearing?)
        # Actually session gate logic: 
        #   if new -> plan
        #   if resume -> preflight
        #   if cancel -> end
        
        # For now, let's implement simple check:
        if not state.waves: # If no waves, likely New (Plan)
            return "plan"
        return "preflight" # Resume

    # builder.add_conditional_edges("session_gate", route_session, {"plan": "plan_or_resume", "preflight": "preflight"})
    # NOTE: We need the actual logic in session_gate to drive this.
    # Placeholder edge for now to link structure
    builder.add_edge("session_gate", "plan_or_resume") 
    
    
    # Plan -> Preflight
    builder.add_edge("plan_or_resume", "preflight")

    # Preflight Routing (Ready vs Input)
    def route_preflight(state: OrchestratorState):
        if state.awaiting_input:
            return END # Interrupt
        return "auth_gate"

    builder.add_conditional_edges(
        "preflight",
        route_preflight,
        {END: END, "auth_gate": "auth_gate"}
    )

    # Auth Routing (Verified vs Auth)
    def route_auth(state: OrchestratorState):
        if state.awaiting_auth:
            return END # Interrupt (Send Flow side effect handled in node)
        return "execute"

    builder.add_conditional_edges(
        "auth_gate",
        route_auth,
        {END: END, "execute": "execute"}
    )

    # Execute -> Advance
    builder.add_edge("execute", "advance")

    # Advance Routing (Loop vs Finish)
    def route_advance(state: OrchestratorState):
        if state.current_wave_index < len(state.waves):
            return "preflight"
        return "summary"

    builder.add_conditional_edges(
        "advance",
        route_advance,
        {"preflight": "preflight", "summary": "summary"}
    )

    # Summary -> End
    builder.add_edge("summary", END)

    return builder.compile()
