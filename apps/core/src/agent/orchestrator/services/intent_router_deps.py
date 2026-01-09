from dataclasses import dataclass
from typing import TYPE_CHECKING
from apps.core.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.core.src.agent.orchestrator.services.flow_manager import FlowContextService
from apps.core.src.agent.orchestrator.services.planner import OrchestratorTaskPlanner
from apps.core.src.agent.orchestrator.services.conversation_responder import ConversationResponder
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.data import DataPurchaseGraph
from apps.core.src.agent.sub_agents.faq import FAQFlowGraph
from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph
from apps.core.src.agent.sub_agents.transfer import TransferService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.services.task_queue import TaskQueueService

@dataclass
class IntentRouterDependencies:
    """Dependencies required by the OrchestratorIntentRouter."""
    task_queue_service: TaskQueueService
    task_planner: OrchestratorTaskPlanner
    transfer_service: TransferService
    airtime_service: AirtimeService
    conversation_responder: ConversationResponder
    context_manager: OrchestratorContextManager
    query_graph: QueryFlowGraph
    whatsapp_client: WhatsAppClient
    flow_context_service: FlowContextService
    data_graph: DataPurchaseGraph | None = None
    account_management_service: AccountManagementService | None = None
    support_graph: SupportFlowGraph | None = None
    faq_graph: FAQFlowGraph | None = None
