"""Transfer Agent - Handles money transfer operations with slot filling and validation."""

from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.graph.state import StateGraph
from langgraph.graph.graph import END

from apps.core.src.agent.core.base_agent import BaseAgent
from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.transfer.nodes import (
    IntentNodes,
    EnrichmentNodes,
    ValidationNodes,
    ClarificationNodes,
    ExecutionNodes,
    ConfirmationNodes,
    ResolutionNodes,
    route_after_slot_validation,
    route_after_validation,
)


class TransferAgent(BaseAgent):
    """Transfer Agent for handling money transfers with slot filling."""

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        temp_llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)

        self.intent_nodes = IntentNodes(temp_llm)
        self.enrichment_nodes = EnrichmentNodes(temp_llm)
        self.validation_nodes = ValidationNodes(temp_llm)
        self.clarification_nodes = ClarificationNodes(temp_llm)
        self.execution_nodes = ExecutionNodes(temp_llm)
        self.confirmation_nodes = ConfirmationNodes(temp_llm)
        self.resolution_nodes = ResolutionNodes()

        super().__init__(llm=llm, model="gpt-4o-mini", temperature=0)

        if hasattr(self.intent_nodes, 'llm'):
            self.intent_nodes.llm = self.llm
        if hasattr(self.enrichment_nodes, 'llm'):
            self.enrichment_nodes.llm = self.llm
        if hasattr(self.validation_nodes, 'llm'):
            self.validation_nodes.llm = self.llm
        if hasattr(self.clarification_nodes, 'llm'):
            self.clarification_nodes.llm = self.llm
        if hasattr(self.execution_nodes, 'llm'):
            self.execution_nodes.llm = self.llm
        if hasattr(self.confirmation_nodes, 'llm'):
            self.confirmation_nodes.llm = self.llm

    def _route_entry(self, state: TransferState) -> str:
        """Conditional entry point: check if continuing a conversation or starting new."""
        if state.get("awaiting_clarification") and state.get("pending_clarification"):
            return "parse_clarification"
        return "intent_parser"

    def _build_graph(self) -> Any:
        """Build the transfer agent graph."""
        graph = StateGraph(TransferState)

        graph.add_node("intent_parser", self.intent_nodes.intent_parser_node)
        graph.add_node("parse_clarification",
                       self.clarification_nodes.parse_clarification_response)
        graph.add_node("context_enricher",
                       self.enrichment_nodes.context_enricher_node)
        graph.add_node("slot_validator",
                       self.validation_nodes.slot_validator_node)
        graph.add_node("account_resolver",
                       self.resolution_nodes.account_resolver_node)
        graph.add_node("clarification_agent",
                       self.clarification_nodes.clarification_agent_node)
        graph.add_node("executor_planner",
                       self.execution_nodes.executor_planner_node)
        graph.add_node("pre_validator",
                       self.execution_nodes.pre_validator_node)
        graph.add_node("confirmation_agent",
                       self.confirmation_nodes.confirmation_agent_node)

        graph.set_conditional_entry_point(
            self._route_entry,
            {
                "intent_parser": "intent_parser",
                "parse_clarification": "parse_clarification",
            }
        )

        graph.add_edge("intent_parser", "context_enricher")
        graph.add_edge("parse_clarification", "slot_validator")
        graph.add_edge("context_enricher", "slot_validator")
        graph.add_conditional_edges(
            "slot_validator",
            route_after_slot_validation,
            {
                "gathering": "clarification_agent",
                "resolving": "account_resolver",
                "planning": "executor_planner",
            },
        )
        graph.add_edge("account_resolver", "slot_validator")
        graph.add_edge("clarification_agent", END)
        graph.add_edge("executor_planner", "pre_validator")
        graph.add_conditional_edges(
            "pre_validator",
            route_after_validation,
            {
                "confirming": "confirmation_agent",
                "completed": END,
            },
        )
        graph.add_edge("confirmation_agent", END)
        return graph.compile(checkpointer=self.memory)

    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """Invoke the transfer agent."""
        initial_state: TransferState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
            "messages": [],
        }

        config = self._get_config(phone_number, message_id)
        result = await self.graph.ainvoke(initial_state, config)
        print(result)
        return result.get("response", "I'm sorry, I couldn't process your transfer request.")
