from dataclasses import dataclass

from apps.core.src.agent.graphs.account_management.service import AccountManagementService
from apps.core.src.agent.graphs.airtime import AirtimeService
from apps.core.src.agent.graphs.data import DataService
from apps.core.src.agent.graphs.faq import FAQService
from apps.core.src.agent.graphs.query import QueryService
from apps.core.src.agent.graphs.support import SupportService
from apps.core.src.agent.graphs.transfer import TransferService
from apps.core.src.agent.orchestrator.pipeline_stages.affirmation.service import FlowContextService
from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager
from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.conversation_responder import ConversationResponder
from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.planner import OrchestratorTaskPlanner
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
    query_service: QueryService
    whatsapp_client: WhatsAppClient
    flow_context_service: FlowContextService
    data_service: DataService | None = None
    account_management_service: AccountManagementService | None = None
    support_service: SupportService | None = None
    faq_service: FAQService | None = None
