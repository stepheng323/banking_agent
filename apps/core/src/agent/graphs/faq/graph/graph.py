"""FAQFlowGraph - LangGraph for informational FAQ queries."""

from collections.abc import Callable
from typing import Any

from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from apps.core.src.agent.graphs.faq.graph.nodes.gate import confidence_gate_node
from apps.core.src.agent.graphs.faq.graph.nodes.guard import final_guard_node
from apps.core.src.agent.graphs.faq.graph.nodes.normalize import normalize_query_node
from apps.core.src.agent.graphs.faq.graph.nodes.retrieve import create_retrieve_node
from apps.core.src.agent.graphs.faq.graph.nodes.synthesize import create_synthesize_node
from apps.core.src.agent.graphs.faq.graph.nodes.validate import validate_intent_node
from apps.core.src.agent.graphs.faq.prompts import SUPPORT_HANDOFF_RESPONSE
from apps.core.src.agent.graphs.faq.retrieval.embeddings import EmbeddingService
from apps.core.src.agent.graphs.faq.state import FAQState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def route_after_validate(state: FAQState) -> str:
    """Route based on validation result."""
    if state.get("is_forbidden_scope"):
        return "support_handoff"
    return "normalize"


def route_after_gate(state: FAQState) -> str:
    """Route based on confidence gate result."""
    if state.get("response"):
        return "end"
    return "synthesize"


class FAQFlowGraph:
    """LangGraph-based FAQ flow for informational queries.

    Architecture:
    - validate_intent → (forbidden?) → support_handoff / normalize
    - normalize → retrieve → confidence_gate
    - confidence_gate → (low confidence?) → end / synthesize
    - synthesize → final_guard → end

    Key Features:
    - Only 1 LLM call (synthesis node)
    - All other nodes are deterministic
    - Forbidden scope queries route to support
    - Low confidence returns uncertainty message
    """

    def __init__(
        self,
        llm: Runnable,
        get_db: Callable[[], Session],
    ):
        self.llm = llm
        self.get_db = get_db
        self.embedding_service = EmbeddingService()
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the FAQ flow graph."""
        graph = StateGraph(FAQState)

        retrieve_node = create_retrieve_node(self.get_db, self.embedding_service)
        synthesize_node = create_synthesize_node(self.llm)

        graph.add_node("validate", validate_intent_node)
        graph.add_node("support_handoff", self._support_handoff_node)
        graph.add_node("normalize", normalize_query_node)
        graph.add_node("retrieve", retrieve_node)
        graph.add_node("confidence_gate", confidence_gate_node)
        graph.add_node("synthesize", synthesize_node)
        graph.add_node("final_guard", final_guard_node)

        graph.set_entry_point("validate")

        graph.add_conditional_edges(
            "validate",
            route_after_validate,
            {
                "support_handoff": "support_handoff",
                "normalize": "normalize",
            },
        )

        graph.add_edge("support_handoff", END)
        graph.add_edge("normalize", "retrieve")
        graph.add_edge("retrieve", "confidence_gate")

        graph.add_conditional_edges(
            "confidence_gate",
            route_after_gate,
            {
                "end": END,
                "synthesize": "synthesize",
            },
        )

        graph.add_edge("synthesize", "final_guard")
        graph.add_edge("final_guard", END)

        return graph.compile()

    def _support_handoff_node(self, state: FAQState) -> FAQState:
        """Generate support handoff response."""
        return {
            **state,
            "response": SUPPORT_HANDOFF_RESPONSE,
            "should_route_to_support": True,
        }

    async def run(
        self,
        phone_number: str,
        message: str,
        message_id: str = "",
    ) -> dict[str, Any]:
        """
        Run the FAQ flow.

        Args:
            phone_number: User's phone number
            message: User's message
            message_id: Optional message ID

        Returns:
            Dict with 'response' and 'should_route_to_support' keys
        """
        initial_state: FAQState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
            "is_forbidden_scope": False,
            "should_route_to_support": False,
        }

        logger.info(f"Running FAQ flow for: {message[:50]}...")

        try:
            final_state = await self.graph.ainvoke(initial_state)

            return {
                "response": final_state.get("response", ""),
                "should_route_to_support": final_state.get("should_route_to_support", False),
                "error": final_state.get("error"),
            }

        except Exception as e:
            logger.error(f"FAQ flow failed: {e}")
            return {
                "response": "I'm having trouble right now. Please try again.",
                "should_route_to_support": False,
                "error": str(e),
            }
