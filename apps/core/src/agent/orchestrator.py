"""Orchestrator Agent - LangGraph supergraph that routes to specialized agents."""

from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph.state import StateGraph
from langgraph.graph.graph import END

from apps.core.src.agent.orchestrator_state import OrchestratorState
from apps.core.src.agent.banking.query.query_agent import QueryAgent
from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent
from apps.core.src.agent.utility.utility_agent import UtilityAgent
from apps.core.src.agent.core.state import AgentState
from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.utility.utility_state import UtilityState
from apps.core.src.agent.conversation_context import ConversationContext
from apps.core.src.agent.models.classification import UnifiedClassification


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

        self.unified_classifier_llm = self.llm.with_structured_output(
            UnifiedClassification)

        self.memory = self.query_agent.memory
        self.graph = self._build_graph()

        self._conversation_contexts: dict[str, ConversationContext] = {}

    def _get_or_create_context(self, phone_number: str) -> ConversationContext:
        """Get existing conversation context or create a new one."""
        if phone_number not in self._conversation_contexts:
            self._conversation_contexts[phone_number] = ConversationContext(
                active_agent="query"
            )

        context = self._conversation_contexts[phone_number]

        if context.is_expired():
            del self._conversation_contexts[phone_number]
            context = ConversationContext(active_agent="query")
            self._conversation_contexts[phone_number] = context

        return context

    async def _classify_intent_node(self, state: OrchestratorState) -> OrchestratorState:
        """
        Node: Unified classification - handles continuation detection and intent classification in one call.
        Reduces cost and latency by using a single LLM call instead of two.
        """
        message = state["message"]
        phone_number = state["phone_number"]

        context = self._get_or_create_context(phone_number)

        context_section = ""
        if context.awaiting_clarification:
            context_section = f"""
ACTIVE CONVERSATION:
- Currently talking to: {context.active_agent.upper()} agent
- Waiting for: {context.clarification_type or 'user input'}

"""

        unified_prompt = f"""You are routing messages in a banking assistant system.
{context_section}USER MESSAGE: "{message}"

{'- STEP 1: Is this a continuation of the active conversation above? (responding to the clarification)' if context.awaiting_clarification else ''}
{'- STEP 2: If not a continuation, classify the intent below:' if context.awaiting_clarification else 'Classify the user intent:'}

Intent categories:
1. **query**: Balance checks, account information (e.g., "What's my balance?", "Show my accounts")
2. **transfer**: Sending money, transfers (e.g., "Send ₦5000 to John", "Transfer money")
3. **utility**: Airtime, data bundles (e.g., "Buy airtime", "I need data bundle")

{'- If continuation: is_continuation=true, intent={context.active_agent}' if context.awaiting_clarification else ''}
{'- If new request: is_continuation=false, classify intent normally' if context.awaiting_clarification else ''}
- Provide confidence (0.0-1.0) and brief reasoning."""

        result = await self.unified_classifier_llm.ainvoke([HumanMessage(content=unified_prompt)])

        if isinstance(result, dict):
            result = UnifiedClassification(**result)

        if result.is_continuation and context.awaiting_clarification:
            print(
                f"🔄 Continuation: {result.reasoning} (confidence: {result.confidence:.2f})")
            state["classified_intent"] = context.active_agent
            state["classification_confidence"] = result.confidence
            state["classification_reasoning"] = f"Continuation: {result.reasoning}"
            context.update_activity()
            return state

        classified_intent = result.intent
        state["classified_intent"] = classified_intent
        state["classification_confidence"] = result.confidence
        state["classification_reasoning"] = result.reasoning
        print(
            f"🎯 Classification: {classified_intent} (confidence: {result.confidence:.2f}) - {result.reasoning}")

        if context.active_agent != classified_intent:
            print(
                f"🔄 Switching from {context.active_agent} to {classified_intent} agent")
            context.switch_agent(classified_intent)

        return state

    async def _route_to_query_agent_node(self, state: OrchestratorState) -> OrchestratorState:
        """Node: Route to QueryAgent subgraph."""
        print(
            f"🎯 ORCHESTRATOR: Routing to QUERY agent | Message: {state['message'][:50]}...")

        phone_number = state["phone_number"]
        context = self._get_or_create_context(phone_number)

        if context.active_agent != "query":
            context.switch_agent("query")

        try:
            # Get existing state from checkpoint to preserve conversation history
            config = {"configurable": {"thread_id": state["phone_number"]}}
            existing_state = await self.query_agent.graph.aget_state(config)

            # Merge new message with existing state
            query_state: AgentState = {
                "phone_number": state["phone_number"],
                "message": state["message"],
                "message_id": state["message_id"],
            }

            # Preserve existing messages and state if present
            if existing_state and existing_state.values:
                existing_values = existing_state.values
                if "messages" in existing_values:
                    query_state["messages"] = existing_values["messages"]
                # Preserve other state fields
                for key in ["user_id", "accounts", "balance", "selected_account"]:
                    if key in existing_values:
                        query_state[key] = existing_values[key]  # type: ignore

            result = await self.query_agent.graph.ainvoke(query_state, config)

            state["response"] = result.get(
                "response", "I'm sorry, I couldn't process your request.")

            awaiting_clarification = result.get("awaiting_clarification")
            clarification_type = result.get("clarification_type")

            if awaiting_clarification:
                context.set_awaiting_clarification(clarification_type)
            else:
                context.clear_awaiting_clarification()

        except Exception as e:
            state["error"] = str(e)
            state["response"] = "I'm sorry, an error occurred while processing your query."
            print(f"❌ Query agent error: {e}")
            context.clear_awaiting_clarification()

        return state

    async def _route_to_transfer_agent_node(self, state: OrchestratorState) -> OrchestratorState:
        """Node: Route to TransferAgent subgraph."""
        print(
            f"🎯 ORCHESTRATOR: Routing to TRANSFER agent | Message: {state['message'][:50]}...")

        phone_number = state["phone_number"]
        context = self._get_or_create_context(phone_number)

        if context.active_agent != "transfer":
            context.switch_agent("transfer")

        try:
            # Get existing state from checkpoint to preserve conversation history
            config = {"configurable": {"thread_id": state["phone_number"]}}
            existing_state = await self.transfer_agent.graph.aget_state(config)

            # Merge new message with existing state
            transfer_state: TransferState = {
                "phone_number": state["phone_number"],
                "message": state["message"],
                "message_id": state["message_id"],
            }

            # Preserve existing state if present (transfer_details, messages, etc.)
            if existing_state and existing_state.values:
                existing_values = existing_state.values
                # Preserve all state fields from checkpoint
                for key in ["messages", "transfer_details", "user_accounts", "user_beneficiaries",
                            "conversation_stage", "missing_slots", "pending_clarification",
                            "awaiting_clarification", "clarification_type", "clarifications_needed",
                            "execution_plan", "validation_result", "dependencies"]:
                    if key in existing_values:
                        # type: ignore
                        transfer_state[key] = existing_values[key]

            # If context indicates we're awaiting clarification, ensure state reflects it
            if context.awaiting_clarification:
                transfer_state["awaiting_clarification"] = True
                if context.clarification_type:
                    transfer_state["clarification_type"] = context.clarification_type

            result = await self.transfer_agent.graph.ainvoke(transfer_state, config)

            state["response"] = result.get(
                "response", "I'm sorry, I couldn't process your transfer request.")

            awaiting_clarification = result.get("awaiting_clarification")
            clarification_type = result.get("clarification_type")

            if awaiting_clarification:
                context.set_awaiting_clarification(clarification_type)
            else:
                conversation_stage = result.get("conversation_stage")
                if conversation_stage == "completed":
                    context.clear_awaiting_clarification()

        except Exception as e:
            state["error"] = str(e)
            state["response"] = "I'm sorry, an error occurred while processing your transfer."
            print(f"❌ Transfer agent error: {e}")
            context.clear_awaiting_clarification()

        return state

    async def _route_to_utility_agent_node(self, state: OrchestratorState) -> OrchestratorState:
        """Node: Route to UtilityAgent subgraph."""
        print(
            f"🎯 ORCHESTRATOR: Routing to UTILITY agent | Message: {state['message'][:50]}...")

        phone_number = state["phone_number"]
        context = self._get_or_create_context(phone_number)

        if context.active_agent != "utility":
            context.switch_agent("utility")

        try:
            # Get existing state from checkpoint to preserve conversation history
            config = {"configurable": {"thread_id": state["phone_number"]}}
            existing_state = await self.utility_agent.graph.aget_state(config)

            # Merge new message with existing state
            utility_state: UtilityState = {
                "phone_number": state["phone_number"],
                "message": state["message"],
                "message_id": state["message_id"],
            }

            # Preserve existing state if present
            if existing_state and existing_state.values:
                existing_values = existing_state.values
                if "messages" in existing_values:
                    utility_state["messages"] = existing_values["messages"]
                # Preserve other utility-specific state
                for key in ["utility_type", "amount", "recipient_phone", "network_provider",
                            "source_account_id", "missing_slots", "conversation_stage"]:
                    if key in existing_values:
                        # type: ignore
                        utility_state[key] = existing_values[key]

            result = await self.utility_agent.graph.ainvoke(utility_state, config)

            state["response"] = result.get(
                "response", "I'm sorry, I couldn't process your utility request.")

            awaiting_clarification = result.get("awaiting_clarification")
            clarification_type = result.get("clarification_type")

            if awaiting_clarification:
                context.set_awaiting_clarification(clarification_type)
            else:
                conversation_stage = result.get("conversation_stage")
                if conversation_stage == "completed":
                    context.clear_awaiting_clarification()

        except Exception as e:
            state["error"] = str(e)
            state["response"] = "I'm sorry, an error occurred while processing your utility request."
            print(f"❌ Utility agent error: {e}")
            context.clear_awaiting_clarification()

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
        """Get standard config for graph invocation.

        Uses only thread_id for checkpoint continuity across messages.
        """
        return {"configurable": {"thread_id": phone_number}}

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
