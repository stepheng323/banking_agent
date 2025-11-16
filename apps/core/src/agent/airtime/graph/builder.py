"""Graph builder for airtime purchase flow."""

from langgraph.graph import StateGraph, END

from apps.core.src.agent.airtime.state import AirtimeState
from apps.core.src.agent.airtime.nodes.extraction import extract_entities


def build_graph():
    """Build the airtime purchase flow graph."""
    # TODO: Implement full graph building logic
    workflow = StateGraph(AirtimeState)
    
    async def extract_node(state: AirtimeState) -> AirtimeState:
        return await extract_entities(state)
    
    workflow.add_node("extract", extract_node)
    workflow.set_entry_point("extract")
    workflow.add_edge("extract", END)
    return workflow

