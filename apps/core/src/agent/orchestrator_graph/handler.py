"""Orchestrator Graph Handler.

Integrates the Top-Level LangGraph into the Message Processing Pipeline.
Replaces the legacy WorkflowHandler.
"""

from typing import TYPE_CHECKING, Any, Literal

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph.state import CompiledStateGraph

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator_graph.factory import AdapterFactory
from apps.core.src.agent.orchestrator_graph.graph import build_orchestrator_graph
from apps.core.src.agent.orchestrator_graph.state import OrchestratorState
from shared.clients.whatsapp.client import WhatsAppClient
from shared.protocols.services import (
    AccountManagementServiceProtocol,
    AirtimeServiceProtocol,
    DataServiceProtocol,
    FAQServiceProtocol,
    QueryServiceProtocol,
    SupportServiceProtocol,
    TransferServiceProtocol,
)
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.planner import OrchestratorTaskPlanner
    from shared.cache.user_data import UserDataCache

logger = get_logger(__name__)


class OrchestratorGraphHandler(MessageHandler):
    """
    Pipeline handler that drives the LangGraph Orchestrator.
    """

    def __init__(
        self,
        task_planner: "OrchestratorTaskPlanner",
        transfer_service: TransferServiceProtocol,
        airtime_service: AirtimeServiceProtocol,
        query_service: QueryServiceProtocol,
        data_service: DataServiceProtocol | None,
        account_management_service: AccountManagementServiceProtocol,
        support_service: SupportServiceProtocol,
        faq_service: FAQServiceProtocol,
        user_cache: "UserDataCache | None" = None,
        redis_client: redis.Redis | None = None,
        whatsapp_client: WhatsAppClient | None = None,
        mode: Literal["planning", "execution", "both"] = "both",
    ):
        super().__init__()
        self.task_planner = task_planner
        self.redis_client = redis_client
        self.whatsapp_client = whatsapp_client
        self.mode = mode

        # optimize services dict
        self.services = {
            "transfer": transfer_service,
            "airtime": airtime_service,
            "query": query_service,
            "data": data_service,
            "manage_accounts": account_management_service,
            "support": support_service,
            "faq": faq_service,
        }
        
        self.adapter_factory = AdapterFactory(self.services, user_cache)
        
        # Build Graph
        # We need checkpointer
        # Assuming redis_client provided is compatible or we need to pass connection string?
        # AsyncRedisSaver usually takes a connection.
        self.checkpointer = None
        if redis_client:
            self.checkpointer = AsyncRedisSaver(redis_client=redis_client)
            
        self.graph: CompiledStateGraph = build_orchestrator_graph()

    async def can_handle(self, context: MessageContext) -> bool:
        """
        Always handle if not yet handled?
        Or logic similar to WorkflowHandler:
        - If active thread exists (checkpointer check?)
        - If planner output exists?
        
        Pattern A says: "One orchestration graph owns turn control".
        So it should probably ALWAYS run unless another specialized handler took over (like routing).
        But we want to replace Planner + Workflow with this.
        So we run if not handled.
        """
        return not context.handled

    async def handle(self, context: MessageContext) -> MessageContext:
        """Run the graph."""
        if not self.checkpointer:
            logger.error("orchestrator_missing_checkpointer")
            return context

        phone_number = context.phone_number
        
        # Prepare inputs
        inputs = {
            "user_id": phone_number,
            "phone_number": phone_number,
            "last_message_text": context.text,
            "last_message_id": context.message_id,
            "flow_callback": context.flow_callback,
            # If planner ran before us? Theoretically we move planner INSIDE.
            # But if pipeline runs planner first, we can accept it.
            # For now, Graph calls planner node.
            # So we don't pass planner_output in inputs unless we want to inject it.
        }
        
        # Config
        config: RunnableConfig = {
            "configurable": {
                "thread_id": phone_number,
                "task_planner": self.task_planner,
                "adapter_factory": self.adapter_factory,
                "whatsapp_client": self.whatsapp_client,
            },
            "recursion_limit": 50,
        }
        
        logger.info("orchestrator_graph_invoke", user=phone_number)
        
        # Invoke Graph
        # We use checkpointer, so we need config with thread_id (set above)
        final_state = await self.graph.ainvoke(inputs, config=config, checkpointer=self.checkpointer)
        
        # Process Output
        # The graph state has 'final_response'.
        response = final_state.get("final_response")
        
        if response:
            return context.with_response(response, handled=True)
            
        # If no response (maybe interrupt?), we might check state flags?
        # But 'summary_node' sets final_response.
        # If interrupt at 'preflight' or 'auth_gate', they also set 'final_response' (the question/prompt).
        
        return context

    async def resume_flow(self, phone_number: str, payload: dict[str, Any]) -> str | None:
        """Resume flow externally (e.g. from auth callback)."""
        if not self.checkpointer:
            return None
            
        inputs = {
            "user_id": phone_number,
            "phone_number": phone_number,
            "flow_callback": payload,
        }
        
        config: RunnableConfig = {
            "configurable": {
                "thread_id": phone_number,
                "task_planner": self.task_planner,
                "adapter_factory": self.adapter_factory,
                "whatsapp_client": self.whatsapp_client,
            },
            "recursion_limit": 50,
        }
        
        logger.info("orchestrator_graph_resume", user=phone_number, payload=payload)
        
        try:
            final_state = await self.graph.ainvoke(inputs, config=config, checkpointer=self.checkpointer)
            return final_state.get("final_response")
        except Exception as e:
            logger.exception("graph_resume_error", error=str(e))
            return None
