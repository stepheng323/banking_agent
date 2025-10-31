# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Utility Agent - Handles airtime and data bundle purchases."""

from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.graph.state import StateGraph
from langgraph.graph.graph import END
from langgraph.checkpoint.memory import MemorySaver

from apps.core.src.agent.core.base_agent import BaseAgent
from apps.core.src.agent.utility.utility_state import UtilityState


class UtilityAgent(BaseAgent):
    """Utility Agent for handling airtime and data bundle purchases."""

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        super().__init__(llm=llm, model="gpt-4o-mini", temperature=0)

    def _build_graph(self) -> Any:
        """Build the utility agent graph."""
        graph = StateGraph(UtilityState)

        # TODO: Implement nodes when ready
        # For now, return a minimal graph that won't crash
        # When nodes are implemented, uncomment and add:
        # graph.add_node("intent_parser", self._intent_parser_node)
        # graph.add_node("network_detector", self._network_detector_node)
        # graph.add_node("slot_validator", self._slot_validator_node)
        # graph.add_node("confirmation", self._confirmation_node)
        # graph.add_node("executor", self._executor_node)
        # graph.set_entry_point("intent_parser")
        # graph.add_edge("intent_parser", END)  # or proper routing

        # Minimal placeholder - just returns immediately
        async def placeholder_node(state: UtilityState) -> UtilityState:
            state["response"] = "Utility agent not yet implemented. Coming soon!"
            state["conversation_stage"] = "completed"
            return state

        graph.add_node("placeholder", placeholder_node)
        graph.set_entry_point("placeholder")
        graph.add_edge("placeholder", END)

        return graph.compile(checkpointer=self.memory)

    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """Invoke the utility agent."""
        initial_state: UtilityState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
            "messages": [],
        }

        config = self._get_config(phone_number, message_id)
        result = await self.graph.ainvoke(initial_state, config)
        return result.get("response", "Utility agent not yet implemented. Coming soon!")
