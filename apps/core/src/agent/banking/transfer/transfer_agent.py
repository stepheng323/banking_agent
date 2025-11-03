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

        # Pass None for beneficiary_repo - will be created per-request with fresh session
        self.intent_nodes = IntentNodes(temp_llm)
        self.enrichment_nodes = EnrichmentNodes(
            temp_llm, beneficiary_repo=None)
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

    def _trim_message_history(self, state: TransferState, max_messages: int = 10) -> None:
        """
        Trim message history to prevent context bloat.

        Keeps only the most recent N messages (default 10 = 5 conversation turns).
        This reduces LLM costs and improves response times.
        """
        messages = state.get("messages", [])
        if messages and len(messages) > max_messages:
            # Keep only the last N messages
            state["messages"] = messages[-max_messages:]
            print(
                f"   ✂️  Trimmed message history: {len(messages)} → {len(state['messages'])} messages")

    def _route_entry(self, state: TransferState) -> str:
        """Conditional entry point: check if continuing a conversation or starting new."""
        # Trim message history to prevent context bloat
        self._trim_message_history(state, max_messages=10)

        awaiting = state.get("awaiting_clarification")
        pending = state.get("pending_clarification")
        transfer_details = state.get("transfer_details", {})
        has_recipient_data = bool(transfer_details.get(
            "recipient", {}).get("account_number"))

        print("🚦 TRANSFER ROUTE ENTRY:")
        print(f"   awaiting_clarification: {awaiting}")
        print(f"   pending_clarification: {pending}")
        print(f"   has_recipient_data: {has_recipient_data}")
        print(f"   message_count: {len(state.get('messages', []))}")
        print(f"   message: {state.get('message', '')[:50]}...")

        # If we're awaiting clarification AND have pending clarification data, parse the response
        if awaiting and pending:
            print("   ✅ Routing to: parse_clarification (continuation)")
            return "parse_clarification"

        # If we already have transfer details with recipient data, skip intent parsing
        if has_recipient_data and transfer_details:
            print("   ✅ Routing to: slot_validator (has existing data)")
            return "slot_validator"

        print("   ✅ Routing to: intent_parser (new conversation)")
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
                "intent_parser": "context_enricher",
                "parse_clarification": "parse_clarification",
            }
        )

        graph.add_edge("context_enricher", "intent_parser")
        graph.add_edge("intent_parser", "slot_validator")
        graph.add_edge("parse_clarification", "slot_validator")
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
        return graph  # Return uncompiled - will be compiled in _ensure_checkpointer()

    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """Invoke the transfer agent."""
        # Ensure checkpointer is ready
        await self._ensure_checkpointer()

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
