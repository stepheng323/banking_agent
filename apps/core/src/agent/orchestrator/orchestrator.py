"""Task-planning orchestrator that coordinates specialized agents using LangGraph."""

from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from shared.config import settings
from shared.clients.whatsapp_client import WhatsAppClient
from apps.core.src.agent.banking.query.query_agent import QueryAgent
from apps.core.src.agent.utility.utility_agent import UtilityAgent
from apps.core.src.agent.conversation_context import ConversationContext
from apps.core.src.agent.orchestrator.state import OrchestratorState
from apps.core.src.agent.models.planner import PlannerOutput
from apps.core.src.agent.services.user_context_loader import UserContextLoader
from apps.core.src.agent.orchestrator.nodes import (
    ContinuationNode,
    QuickIntentClassifierNode,
    ContextLoaderNode,
    PlanningNode,
    ConversationalNode,
    TaskExecutorNode,
    ResponseFormatterNode,
    route_after_continuation_check,
    route_after_quick_classification,
    route_after_planning,
    route_after_execution,
)
from apps.core.src.agent.orchestrator.nodes.cancel import GlobalCancelNode
from apps.core.src.agent.orchestrator.nodes.pending_switch import PendingSwitchNode
from apps.core.src.agent.orchestrator.nodes.pending_switch_response import PendingSwitchResponseNode
from apps.core.src.agent.orchestrator.services import (
    AgentInvoker,
    TaskPlanner,
    TaskExecutor,
    ResponseFormatter,
)


class OrchestratorAgent:
    """
    High-level orchestrator that uses an LLM planner to generate task lists
    and delegates execution to specialized agent subgraphs.

    This orchestrator follows proper LangGraph methodology with:
    - Modular node architecture
    - Separated service layer
    - Clean routing logic
    - State management via checkpoints
    """

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        """Initialize orchestrator with specialized agents and build LangGraph."""
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)

        self.query_agent = QueryAgent(self.llm)
        self.utility_agent = UtilityAgent(self.llm)

        self.planner_llm = self.llm.with_structured_output(PlannerOutput)
        self.response_formatter_llm = self.llm

        self.context_loader = UserContextLoader()

        # Shared WhatsApp client instance (reads credentials from env)
        self.whatsapp_client = WhatsAppClient()

        self.agent_invoker = AgentInvoker(
            self.query_agent, self.utility_agent, self.whatsapp_client
        )
        self.task_planner = TaskPlanner(self.planner_llm)
        self.task_executor = TaskExecutor(self.agent_invoker)
        self.response_formatter = ResponseFormatter(
            self.response_formatter_llm)

        self._conversation_contexts: dict[str, ConversationContext] = {}

        self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(
            conn_string=settings.database_url
        )

        # Will be set when context manager is entered
        self.memory = None
        self._checkpointer_setup = False
        self._graph_compiled = False

        # Build graph structure (but don't compile yet - checkpointer not ready)
        self._graph_builder = self._build_graph()
        self.graph = None  # Will be compiled after checkpointer is ready

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            self.memory = await self._checkpointer_cm.__aenter__()
            self._checkpointer_setup = True

        # Compile graph now that checkpointer is ready
        if not self._graph_compiled:
            self.graph = self._graph_builder.compile(checkpointer=self.memory)
            self._graph_compiled = True

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

    def _build_graph(self) -> Any:
        """Build the orchestrator LangGraph with modular nodes."""
        graph = StateGraph(OrchestratorState)

        def get_orchestrator_lambda():
            return self

        continuation_node = ContinuationNode(
            self._get_or_create_context, orchestrator_instance=self)
        quick_classifier_node = QuickIntentClassifierNode(self.llm)
        # global cancel node needs a transfer agent instance; reuse router cache if present

        def _get_transfer_agent_cached():
            from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent
            return TransferAgent()

        global_cancel_node = GlobalCancelNode(
            _get_transfer_agent_cached, orchestrator_instance=self, get_context_func=self._get_or_create_context)
        pending_switch_node = PendingSwitchNode(_get_transfer_agent_cached)
        pending_switch_response_node = PendingSwitchResponseNode(
            _get_transfer_agent_cached, orchestrator_instance=self)
        context_loader_node = ContextLoaderNode(self.context_loader)
        planning_node = PlanningNode(self.task_planner.plan_tasks)
        conversational_node = ConversationalNode(self.llm)

        task_executor_node = TaskExecutorNode(
            self.task_executor.execute_task_plan,
            self._get_or_create_context,
            get_orchestrator_lambda
        )
        response_formatter_node = ResponseFormatterNode(
            self.response_formatter.format_final_response
        )

        async def check_continuation_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await continuation_node(state)

        async def quick_classify_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await quick_classifier_node(state)

        async def load_context_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await context_loader_node(state)

        async def planner_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await planning_node(state)

        async def conversational_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await conversational_node(state)

        async def task_executor_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await task_executor_node(state)

        async def response_formatter_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await response_formatter_node(state)

        graph.add_node("check_continuation", check_continuation_wrapper)
        graph.add_node("quick_classify", quick_classify_wrapper)

        async def global_cancel_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await global_cancel_node(state)

        async def pending_switch_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await pending_switch_node(state)

        async def pending_switch_response_wrapper(state: OrchestratorState) -> OrchestratorState:
            return await pending_switch_response_node(state)

        graph.add_node("load_context", load_context_wrapper)
        graph.add_node("handle_global_cancel", global_cancel_wrapper)
        graph.add_node("pending_switch", pending_switch_wrapper)
        graph.add_node("handle_pending_switch_response",
                       pending_switch_response_wrapper)
        graph.add_node("planner", planner_wrapper)
        graph.add_node("conversational", conversational_wrapper)
        graph.add_node("task_executor", task_executor_wrapper)
        graph.add_node("response_formatter", response_formatter_wrapper)

        graph.set_entry_point("check_continuation")

        graph.add_conditional_edges(
            "check_continuation",
            route_after_continuation_check,
            {
                "handle_global_cancel": "handle_global_cancel",
                "handle_pending_switch_response": "handle_pending_switch_response",
                "task_executor": "task_executor",
                "quick_classify": "quick_classify",
            }
        )

        def _route_after_quick(state: OrchestratorState) -> str:
            global_cancel = state.get("global_cancel")
            new_transfer = state.get("new_transfer")
            clarification_type = state.get("clarification_type")

            print(
                f"🔀 ROUTE AFTER QUICK: global_cancel={global_cancel}, new_transfer={new_transfer}, clarification_type={clarification_type}")

            if global_cancel:
                print("   ✅ Routing to handle_global_cancel")
                return "handle_global_cancel"

            # If we're already in pending_switch_confirmation, handle the response
            if clarification_type == "pending_switch_confirmation":
                print("   ✅ Routing to handle_pending_switch_response")
                return "handle_pending_switch_response"

            if new_transfer:
                print("   ✅ Routing to pending_switch")
                return "pending_switch"

            route = route_after_quick_classification(state)
            print(f"   ✅ Routing to {route}")
            return route

        graph.add_conditional_edges(
            "quick_classify",
            _route_after_quick,
            {
                "handle_global_cancel": "handle_global_cancel",
                "pending_switch": "pending_switch",
                "handle_pending_switch_response": "handle_pending_switch_response",
                "conversational": "conversational",
                "load_context": "load_context",
            }
        )

        graph.add_edge("handle_global_cancel", END)

        def _route_after_pending_switch(state: OrchestratorState) -> str:
            if state.get("route_fallback"):
                return "load_context"
            return END

        graph.add_conditional_edges(
            "pending_switch",
            _route_after_pending_switch,
            {
                "load_context": "load_context",
                END: END,
            }
        )

        def _route_after_pending_switch_response(state: OrchestratorState) -> str:
            if state.get("awaiting_clarification") and state.get("clarification_type") == "pending_switch_confirmation":
                # Unclear response, ask again (response already set)
                return END

            # If response is already set (from direct transfer agent invocation for "old"), route to END
            if state.get("response"):
                return END

            if state.get("active_agent") == "transfer":
                # User chose "old", but response not set yet - route to task_executor to continue with transfer agent
                return "task_executor"
            # User chose "new", route to load_context to process new instruction
            return "load_context"

        graph.add_conditional_edges(
            "handle_pending_switch_response",
            _route_after_pending_switch_response,
            {
                END: END,
                "task_executor": "task_executor",
                "load_context": "load_context",
            }
        )

        graph.add_edge("load_context", "planner")

        graph.add_conditional_edges(
            "planner",
            route_after_planning,
            {
                "conversational": "conversational",
                "task_executor": "task_executor",
                END: END,
            }
        )

        graph.add_edge("conversational", END)

        graph.add_conditional_edges(
            "task_executor",
            route_after_execution,
            {
                END: END,
                "response_formatter": "response_formatter",
            }
        )

        graph.add_edge("response_formatter", END)

        return graph  # Return uncompiled - will be compiled in _ensure_checkpointer()

    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """
        Run the planner-driven orchestration loop using LangGraph.

        The graph automatically handles:
        - Continuation detection
        - Context loading
        - Typo correction
        - Intent disambiguation
        - Task planning
        - Conversational responses
        - Task execution
        - Response formatting
        """
        # Ensure checkpointer is ready
        await self._ensure_checkpointer()

        initial_state: OrchestratorState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
        }

        config = {"configurable": {"thread_id": phone_number}}

        try:
            result = await self.graph.ainvoke(initial_state, config)
            return result.get("response", "I'm sorry, I couldn't process your request.")
        except Exception as e:
            print(f"❌ Orchestrator graph error: {e}")
            import traceback
            traceback.print_exc()
            return "I'm sorry, an error occurred while processing your request. Please try again."
