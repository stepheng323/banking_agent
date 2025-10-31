# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false
"""Orchestrator Agent - LangGraph supergraph that routes to specialized agents."""

from typing import Any, Literal, cast

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph.state import StateGraph
from langgraph.graph.graph import END
from pydantic import BaseModel

from apps.core.src.agent.orchestrator_state import OrchestratorState
from apps.core.src.agent.banking.query.query_agent import QueryAgent
from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent
from apps.core.src.agent.utility.utility_agent import UtilityAgent
from apps.core.src.agent.core.state import AgentState
from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.utility.utility_state import UtilityState


class IntentClassification(BaseModel):
    """Structured output for intent classification."""
    intent: Literal["query", "transfer", "utility"]
    confidence: float
    reasoning: str


class OrchestratorAgent:
    """
    Orchestrator as a LangGraph supergraph that routes to specialized agent subgraphs.

    Follows LangGraph best practices:
    - Uses StateGraph for orchestration
    - Routes conditionally to subgraphs
    - Handles state transformation between supergraph and subgraphs
    """

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        """Initialize orchestrator with specialized agents."""
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)

        self.query_agent = QueryAgent(self.llm)
        self.transfer_agent = TransferAgent(self.llm)
        self.utility_agent = UtilityAgent(self.llm)

        self.classifier_llm = self.llm.with_structured_output(
            IntentClassification)

        # Set memory BEFORE building graph (graph needs it for checkpointer)
        self.memory = self.query_agent.memory
        self.graph = self._build_graph()

    async def _classify_intent_node(self, state: OrchestratorState) -> OrchestratorState:
        """
        Node: Classify user intent using LLM with structured output.
        """
        message = state["message"]

        classification_prompt = f"""Classify the user's banking request into one of these categories:

1. **query**: Balance checks, account information, account listing, checking balance
   Examples: "What's my balance?", "Show my accounts", "How much do I have?"

2. **transfer**: Sending money, transfers, payments, sending funds to someone
   Examples: "Send ₦5000 to John", "Transfer money to mummy", "Pay my rent"

3. **utility**: Airtime purchases, data bundles, top-ups, recharging
   Examples: "Buy airtime", "I need data bundle", "Top up my phone"

User message: "{message}"

Classify this message. Be accurate and consider context."""

        try:
            result = await self.classifier_llm.ainvoke([HumanMessage(content=classification_prompt)])

            if isinstance(result, IntentClassification):
                state["classified_intent"] = result.intent
                state["classification_confidence"] = result.confidence
                state["classification_reasoning"] = result.reasoning
                print(
                    f"🎯 Classification: {result.intent} (confidence: {result.confidence:.2f}) - {result.reasoning}")
            else:
                # Fallback if structured output fails
                intent_str = result.get("intent", "query") if isinstance(
                    result, dict) else "query"
                state["classified_intent"] = cast(
                    Literal["query", "transfer", "utility"], intent_str)
                state["classification_confidence"] = 0.5
                state["classification_reasoning"] = "Fallback classification"

        except Exception as e:
            print(f"⚠️ Classification error: {e}, defaulting to query")
            # Fallback to keyword-based classification
            message_lower = message.lower()

            utility_keywords = [
                "airtime", "data", "bundle", "top up", "recharge",
                "buy airtime", "buy data", "purchase airtime", "purchase data"
            ]
            if any(kw in message_lower for kw in utility_keywords):
                state["classified_intent"] = "utility"
            elif any(kw in message_lower for kw in ["send", "transfer", "pay", "give", "send money", "transfer to", "send to", "wire", "remit"]):
                state["classified_intent"] = "transfer"
            else:
                state["classified_intent"] = "query"

            state["classification_confidence"] = 0.3
            state["classification_reasoning"] = f"Keyword-based fallback: {str(e)}"

        return state

    async def _route_to_query_agent_node(self, state: OrchestratorState) -> OrchestratorState:
        """Node: Route to QueryAgent subgraph."""
        print(
            f"🎯 ORCHESTRATOR: Routing to QUERY agent | Message: {state['message'][:50]}...")

        try:
            # Transform state for QueryAgent
            query_state: AgentState = {
                "phone_number": state["phone_number"],
                "message": state["message"],
                "message_id": state["message_id"],
                "messages": [],
            }

            # Invoke QueryAgent subgraph
            config = {"configurable": {
                "thread_id": state["phone_number"], "thread_ts": state["message_id"]}}
            result = await self.query_agent.graph.ainvoke(query_state, config)

            state["response"] = result.get(
                "response", "I'm sorry, I couldn't process your request.")

        except Exception as e:
            state["error"] = str(e)
            state["response"] = "I'm sorry, an error occurred while processing your query."
            print(f"❌ Query agent error: {e}")

        return state

    async def _route_to_transfer_agent_node(self, state: OrchestratorState) -> OrchestratorState:
        """Node: Route to TransferAgent subgraph."""
        print(
            f"🎯 ORCHESTRATOR: Routing to TRANSFER agent | Message: {state['message'][:50]}...")

        try:
            # Transform state for TransferAgent
            transfer_state: TransferState = {
                "phone_number": state["phone_number"],
                "message": state["message"],
                "message_id": state["message_id"],
                "messages": [],
            }

            # Invoke TransferAgent subgraph
            config = {"configurable": {
                "thread_id": state["phone_number"], "thread_ts": state["message_id"]}}
            result = await self.transfer_agent.graph.ainvoke(transfer_state, config)

            state["response"] = result.get(
                "response", "I'm sorry, I couldn't process your transfer request.")

        except Exception as e:
            state["error"] = str(e)
            state["response"] = "I'm sorry, an error occurred while processing your transfer."
            print(f"❌ Transfer agent error: {e}")

        return state

    async def _route_to_utility_agent_node(self, state: OrchestratorState) -> OrchestratorState:
        """Node: Route to UtilityAgent subgraph."""
        print(
            f"🎯 ORCHESTRATOR: Routing to UTILITY agent | Message: {state['message'][:50]}...")

        try:
            # Transform state for UtilityAgent
            utility_state: UtilityState = {
                "phone_number": state["phone_number"],
                "message": state["message"],
                "message_id": state["message_id"],
                "messages": [],
            }

            # Invoke UtilityAgent subgraph
            config = {"configurable": {
                "thread_id": state["phone_number"], "thread_ts": state["message_id"]}}
            result = await self.utility_agent.graph.ainvoke(utility_state, config)

            state["response"] = result.get(
                "response", "I'm sorry, I couldn't process your utility request.")

        except Exception as e:
            state["error"] = str(e)
            state["response"] = "I'm sorry, an error occurred while processing your utility request."
            print(f"❌ Utility agent error: {e}")

        return state

    def _route_to_agent(self, state: OrchestratorState) -> str:
        """
        Conditional routing function: Routes to appropriate agent subgraph.
        """
        intent = state.get("classified_intent", "query")

        if intent == "transfer":
            return "transfer_agent"
        elif intent == "utility":
            return "utility_agent"
        else:
            return "query_agent"

    def _build_graph(self) -> Any:
        """
        Build the orchestrator supergraph.

        Flow:
        1. Classify intent (classification node)
        2. Route conditionally to appropriate agent subgraph
        3. Return response
        """
        graph = StateGraph(OrchestratorState)

        graph.add_node("classify_intent", self._classify_intent_node)
        graph.add_node("query_agent", self._route_to_query_agent_node)
        graph.add_node("transfer_agent", self._route_to_transfer_agent_node)
        graph.add_node("utility_agent", self._route_to_utility_agent_node)

        graph.set_entry_point("classify_intent")

        graph.add_conditional_edges(
            "classify_intent",
            self._route_to_agent,
            {
                "query_agent": "query_agent",
                "transfer_agent": "transfer_agent",
                "utility_agent": "utility_agent",
            },
        )

        graph.add_edge("query_agent", END)
        graph.add_edge("transfer_agent", END)
        graph.add_edge("utility_agent", END)

        return graph.compile(checkpointer=self.memory)

    def _get_config(self, phone_number: str, message_id: str) -> dict[str, Any]:
        """Get standard config for graph invocation."""
        return {"configurable": {"thread_id": phone_number, "thread_ts": message_id}}

    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """
        Invoke the orchestrator supergraph.

        Args:
            phone_number: User's phone number
            message: User's message
            message_id: Unique message identifier

        Returns:
            Agent response string
        """
        initial_state: OrchestratorState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
        }

        config = self._get_config(phone_number, message_id)
        result = await self.graph.ainvoke(initial_state, config)

        return result.get("response", "I'm sorry, I couldn't process your request.")
